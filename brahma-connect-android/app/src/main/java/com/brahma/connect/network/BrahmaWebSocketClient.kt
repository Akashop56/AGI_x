package com.brahma.connect.network

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.brahma.connect.audio.PhoneAudioEngine
import com.brahma.connect.commands.DeviceCommandHandler
import com.brahma.connect.core.AgentStateStore
import com.brahma.connect.core.BrahmaProtocol
import com.brahma.connect.core.ChatMessage
import com.brahma.connect.core.ConnectionState
import com.brahma.connect.core.DeviceCredential
import com.brahma.connect.core.DeviceSnapshot
import com.brahma.connect.core.GatewayEndpoint
import com.brahma.connect.core.PairingOffer
import com.brahma.connect.pairing.PairingStorage
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.math.min

class BrahmaWebSocketClient(
    private val context: Context,
    private val storage: PairingStorage,
    private val commandHandler: DeviceCommandHandler,
) {
    companion object {
        private const val CONNECT_TIMEOUT_SECONDS = 15L
        private const val CONNECT_TIMEOUT_MS = CONNECT_TIMEOUT_SECONDS * 1_000L
        private const val MAX_RECONNECT_DELAY_MS = 30_000L
    }

    /** Registered by the foreground service once the user granted RECORD_AUDIO. */
    var audioEngine: PhoneAudioEngine? = null
    /** Optional UI bridge (used to surface headless UI events in the app). */
    var onUiEvent: ((String, JSONObject) -> Unit)? = null

    /*
     * WebSocket read timeouts are deliberately disabled: a connected gateway is
     * long-lived and is kept alive by OkHttp's ping interval.  The explicit
     * connect watchdog below still makes a stalled initial handshake visible to
     * the user instead of leaving the visualizer in CONNECTING forever.
     */
    private val client = OkHttpClient.Builder()
        .retryOnConnectionFailure(true)
        .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .writeTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    private val mainHandler = Handler(Looper.getMainLooper())
    private val connectionLock = Any()

    @Volatile
    private var socket: WebSocket? = null
    @Volatile
    private var currentEndpoint: GatewayEndpoint? = null
    @Volatile
    private var currentOffer: PairingOffer? = null
    @Volatile
    private var currentCredential: DeviceCredential? = null
    @Volatile
    private var manualDisconnect = false
    @Volatile
    private var reconnectAttempt = 0

    /** Monotonically increasing token used to ignore callbacks from old sockets. */
    @Volatile
    private var connectionAttempt = 0L
    private var handledAttempt = -1L
    private var connectTimeoutTask: Runnable? = null
    private var reconnectTask: Runnable? = null

    fun connect(
        endpoint: GatewayEndpoint,
        credential: DeviceCredential? = storage.loadCredential(),
        offer: PairingOffer? = null,
    ) {
        var oldSocket: WebSocket? = null
        var attempt = 0L

        synchronized(connectionLock) {
            if (socket != null && currentEndpoint == endpoint && !manualDisconnect) {
                currentCredential = credential ?: currentCredential
                currentOffer = offer ?: currentOffer
                AgentStateStore.setGateway(endpoint)
                return
            }

            currentEndpoint = endpoint
            currentCredential = credential
            currentOffer = offer
            manualDisconnect = false
            reconnectAttempt = 0
            connectionAttempt += 1
            attempt = connectionAttempt
            handledAttempt = -1L
            oldSocket = socket
            socket = null
        }

        cancelConnectTimeout()
        cancelReconnect()
        oldSocket?.close(1000, "Reconnecting")

        AgentStateStore.setGateway(endpoint)
        AgentStateStore.setConnectionState(ConnectionState.CONNECTING)
        AgentStateStore.setStatus("Connecting to ${endpoint.name}")
        AgentStateStore.setError(null)

        val request = runCatching {
            Request.Builder()
                .url(webSocketUrl(endpoint))
                .build()
        }.getOrElse { error ->
            handleConnectionFailure(
                attempt = attempt,
                webSocket = null,
                message = "Invalid gateway address ${endpoint.host}:${endpoint.port}: ${error.safeMessage()}",
            )
            return
        }

        // OkHttp normally reports a failed TCP/TLS handshake, but a blocked or
        // unreachable local address can still leave a request pending.  This
        // watchdog guarantees a visible error and schedules a bounded retry.
        scheduleConnectTimeout(attempt, endpoint)
        val listener = BrahmaSocketListener(attempt)
        val newSocket = runCatching { client.newWebSocket(request, listener) }.getOrElse { error ->
            handleConnectionFailure(
                attempt = attempt,
                webSocket = null,
                message = formatFailure(endpoint, error, null),
            )
            return
        }

        synchronized(connectionLock) {
            if (attempt == connectionAttempt && !manualDisconnect && handledAttempt != attempt) {
                socket = newSocket
            } else {
                // The user may have disconnected while newWebSocket was being
                // scheduled. Do not allow this stale socket to reconnect.
                newSocket.cancel()
            }
        }
    }

    fun disconnect() {
        val activeSocket: WebSocket?
        synchronized(connectionLock) {
            manualDisconnect = true
            connectionAttempt += 1
            handledAttempt = connectionAttempt
            activeSocket = socket
            socket = null
        }
        cancelConnectTimeout()
        cancelReconnect()
        audioEngine?.stop()
        activeSocket?.close(1000, "Disconnected by user")
        AgentStateStore.setConnectionState(ConnectionState.DISCONNECTED)
        AgentStateStore.setStatus("Disconnected")
        AgentStateStore.setError(null)
    }

    private fun scheduleConnectTimeout(attempt: Long, endpoint: GatewayEndpoint) {
        cancelConnectTimeout()
        val timeoutTask = Runnable {
            if (isCurrentAttempt(attempt)) {
                val activeSocket = socket
                val handled = handleConnectionFailure(
                    attempt = attempt,
                    webSocket = activeSocket,
                    message = "Connection timed out while reaching ${endpoint.host}:${endpoint.port}.",
                )
                if (handled) activeSocket?.cancel()
            }
        }
        connectTimeoutTask = timeoutTask
        mainHandler.postDelayed(timeoutTask, CONNECT_TIMEOUT_MS)
    }

    private fun cancelConnectTimeout() {
        connectTimeoutTask?.let(mainHandler::removeCallbacks)
        connectTimeoutTask = null
    }

    private fun cancelReconnect() {
        reconnectTask?.let(mainHandler::removeCallbacks)
        reconnectTask = null
    }

    private fun reconnectLater(attempt: Long) {
        val endpoint = currentEndpoint ?: return
        val delayMs: Long
        val task: Runnable
        synchronized(connectionLock) {
            if (manualDisconnect || attempt != connectionAttempt || reconnectTask != null) return
            reconnectAttempt += 1
            delayMs = min(MAX_RECONNECT_DELAY_MS, 1_000L * (1 shl min(reconnectAttempt, 5)))
            task = Runnable {
                val shouldReconnect = synchronized(connectionLock) {
                    reconnectTask = null
                    !manualDisconnect && attempt == connectionAttempt
                }
                if (shouldReconnect) {
                    connect(endpoint, currentCredential, currentOffer)
                }
            }
            reconnectTask = task
        }

        AgentStateStore.setConnectionState(ConnectionState.RECONNECTING)
        AgentStateStore.setStatus("Reconnecting in ${delayMs / 1000}s")
        mainHandler.postDelayed(task, delayMs)
    }

    /**
     * Mark an attempt as failed exactly once. OkHttp can report both a close
     * and a failure around cancellation, so the attempt token prevents stale
     * callbacks from overwriting a newer connection's state.
     */
    private fun handleConnectionFailure(attempt: Long, webSocket: WebSocket?, message: String): Boolean {
        val accepted = synchronized(connectionLock) {
            if (manualDisconnect || attempt != connectionAttempt || handledAttempt == attempt) {
                false
            } else {
                handledAttempt = attempt
                if (webSocket == null || socket === webSocket) socket = null
                true
            }
        }
        if (!accepted) return false

        cancelConnectTimeout()
        AgentStateStore.addLog(message)
        AgentStateStore.setConnectionState(ConnectionState.DISCONNECTED)
        AgentStateStore.setStatus("Connection failed")
        AgentStateStore.setError(message)
        reconnectLater(attempt)
        return true
    }

    private fun isCurrentAttempt(attempt: Long): Boolean = synchronized(connectionLock) {
        !manualDisconnect && attempt == connectionAttempt && handledAttempt != attempt
    }

    private fun isCurrentSocket(webSocket: WebSocket, attempt: Long): Boolean = synchronized(connectionLock) {
        !manualDisconnect &&
            attempt == connectionAttempt &&
            handledAttempt != attempt &&
            (socket == null || socket === webSocket)
    }

    private fun webSocketUrl(endpoint: GatewayEndpoint): String {
        // NSD can return an IPv6 literal. Bracket it so Request.Builder parses
        // it correctly; IPv4 addresses such as 127.0.0.1 remain unchanged.
        val host = endpoint.host.trim()
        val formattedHost = if (host.contains(":") && !host.startsWith("[")) "[$host]" else host
        return "ws://$formattedHost:${endpoint.port}/ws"
    }

    private fun send(json: JSONObject): Boolean {
        val activeSocket = socket
        if (activeSocket == null) {
            reportTransportError("Cannot send to the gateway: the WebSocket is not connected.")
            return false
        }

        return try {
            if (activeSocket.send(json.toString())) {
                true
            } else {
                reportTransportError("The gateway rejected a WebSocket message.")
                false
            }
        } catch (error: Throwable) {
            reportTransportError("Could not send to the gateway: ${error.safeMessage()}")
            false
        }
    }

    private fun reportTransportError(message: String) {
        AgentStateStore.addLog(message)
        AgentStateStore.setError(message)
        AgentStateStore.setStatus("Gateway unavailable")
    }

    private fun formatFailure(
        endpoint: GatewayEndpoint,
        error: Throwable,
        response: okhttp3.Response?,
    ): String {
        val detail = error.safeMessage()
        val responseDetail = response?.let {
            "HTTP ${it.code}${it.message.takeIf(String::isNotBlank)?.let { message -> " $message" } ?: ""}"
        }
        val cleartextHint = if (detail.contains("cleartext", ignoreCase = true)) {
            " Android blocked cleartext traffic; verify that the app manifest allows local ws:// connections."
        } else {
            ""
        }
        return buildString {
            append("Unable to connect to ${endpoint.host}:${endpoint.port}. ")
            append(responseDetail ?: detail)
            append(cleartextHint)
        }
    }

    private fun Throwable.safeMessage(): String = message?.trim().takeUnless { it.isNullOrBlank() }
        ?: javaClass.simpleName.ifBlank { "Unknown network error" }

    private fun batterySnapshot(): Pair<Int, Boolean> {
        val battery = commandHandler.handle("get_battery", emptyMap()).data
        val percentage = (battery["percentage"] as? Int) ?: -1
        val charging = (battery["charging"] as? Boolean) ?: false
        return percentage to charging
    }

    private fun buildSnapshot(): DeviceSnapshot {
        val credential = currentCredential
        val (percentage, charging) = batterySnapshot()
        return DeviceSnapshot(
            deviceId = credential?.deviceId,
            deviceName = credential?.deviceName ?: android.os.Build.MODEL ?: "Android",
            androidVersion = android.os.Build.VERSION.RELEASE ?: "Unknown",
            agentVersion = "1.0.0",
            batteryPercentage = percentage,
            charging = charging,
            wifiEnabled = true,
            capabilities = listOf(
                "device_info",
                "battery",
                "flashlight",
                "volume",
                "media",
                "launch_app",
                "apps",
                "open_url",
                "wifi_state",
                // Headless-brain body features: mic streaming, voice playback,
                // notification mirroring and screen sharing (see PhoneAudioEngine).
                "audio_in",
                "audio_out",
                "notifications",
                "screen_state",
            ),
        )
    }

    private fun sendHello() {
        if (send(BrahmaProtocol.hello(buildSnapshot()))) {
            AgentStateStore.setStatus("Awaiting approval")
        }
    }

    private fun sendPairRequest() {
        val offer = currentOffer ?: run {
            sendHello()
            return
        }
        val snapshot = buildSnapshot().toJson()
            .put("pairing_token", offer.pairingToken)
            .put("pairing_code", offer.pairingCode)
        if (send(BrahmaProtocol.envelope(BrahmaProtocol.PAIR_REQUEST, snapshot))) {
            AgentStateStore.setStatus("Pairing request sent")
        }
    }

    private fun sendAuthenticate() {
        val credential = currentCredential ?: storage.loadCredential()
        if (credential == null) {
            sendHello()
            return
        }
        currentCredential = credential
        if (send(BrahmaProtocol.authenticate(credential))) {
            AgentStateStore.setStatus("Authenticating")
        }
    }

    private fun handleCommandMessage(root: JSONObject) {
        val requestId = root.optString("request_id")
        val payload = root.optJSONObject("payload") ?: JSONObject()
        val action = payload.optString("action")
        val params = payload.optJSONObject("parameters") ?: JSONObject()
        val parameterMap = buildMap<String, Any?> {
            params.keys().forEach { key ->
                put(key, params.opt(key))
            }
        }
        val result = commandHandler.handle(action, parameterMap)
        val response = BrahmaProtocol.envelope(
            BrahmaProtocol.RESULT,
            result.toJson(),
            requestId = requestId,
        )
        send(response)
    }

    private fun handlePairApproved(root: JSONObject) {
        val payload = root.optJSONObject("payload") ?: JSONObject()
        val device = payload.optJSONObject("device") ?: JSONObject()
        val secret = payload.optString("device_secret")
        val deviceId = device.optString("device_id")
        val deviceName = device.optString("name", android.os.Build.MODEL ?: "Android")
        val credential = DeviceCredential(
            deviceId = deviceId,
            deviceSecret = secret,
            deviceName = deviceName,
            gatewayHost = currentEndpoint?.host.orEmpty(),
            gatewayPort = currentEndpoint?.port ?: 8765,
        )
        storage.saveCredential(credential)
        currentCredential = credential
        AgentStateStore.setCredential(credential)
        AgentStateStore.setStatus("Pair approved")
        sendAuthenticate()
    }

    private inner class BrahmaSocketListener(
        private val attempt: Long,
    ) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: okhttp3.Response) {
            if (!isCurrentSocket(webSocket, attempt)) {
                webSocket.close(1000, "Stale connection")
                return
            }
            synchronized(connectionLock) {
                if (attempt == connectionAttempt) socket = webSocket
            }
            cancelConnectTimeout()
            reconnectAttempt = 0
            AgentStateStore.setConnectionState(ConnectionState.CONNECTING)
            AgentStateStore.setError(null)
            runCatching {
                val storedCredential = storage.loadCredential()
                if (currentCredential != null || storedCredential != null) {
                    sendAuthenticate()
                } else if (currentOffer != null) {
                    sendPairRequest()
                } else {
                    sendHello()
                }
            }.onFailure { error ->
                val message = "Could not initialize the gateway handshake: ${error.safeMessage()}"
                if (handleConnectionFailure(attempt, webSocket, message)) webSocket.cancel()
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!isCurrentSocket(webSocket, attempt)) return
            runCatching {
                val root = JSONObject(text)
                val type = root.optString("type")
                when (type) {
                    BrahmaProtocol.PAIR_REQUEST -> {
                        AgentStateStore.setStatus("Waiting for approval")
                    }
                    BrahmaProtocol.PAIR_APPROVED -> handlePairApproved(root)
                    BrahmaProtocol.DEVICE_ONLINE -> {
                        AgentStateStore.setConnectionState(ConnectionState.CONNECTED)
                        AgentStateStore.setStatus("Connected")
                        AgentStateStore.setError(null)
                    }
                    BrahmaProtocol.CAPABILITIES -> {
                        AgentStateStore.addLog("Capabilities synced")
                    }
                    BrahmaProtocol.EXECUTE -> handleCommandMessage(root)
                    BrahmaProtocol.PING -> {
                        send(
                            BrahmaProtocol.envelope(
                                BrahmaProtocol.PONG,
                                JSONObject().put("status", "ok"),
                                requestId = root.optString("request_id"),
                            )
                        )
                    }
                    BrahmaProtocol.ERROR -> {
                        val error = root.optJSONObject("payload")?.optString("error")
                            ?.takeIf { it.isNotBlank() }
                            ?: "The gateway returned an error."
                        AgentStateStore.addLog("Gateway error: $error")
                        AgentStateStore.setStatus("Gateway error")
                        AgentStateStore.setError(error)
                    }
                    BrahmaProtocol.CHAT_MESSAGE -> {
                        val payload = root.optJSONObject("payload") ?: return
                        val role = payload.optString("role", "system")
                        val text = payload.optString("text", "")
                        val msgId = root.optString("request_id")
                        val timestamp = System.currentTimeMillis()
                        val msg = ChatMessage(msgId, role, text, timestamp, "Sent")
                        AgentStateStore.addChatMessage(msg)
                    }
                    BrahmaProtocol.EVENT -> handleEvent(root)
                }
            }.onFailure {
                val message = "Invalid message from gateway: ${it.safeMessage()}"
                AgentStateStore.addLog(message)
                AgentStateStore.setStatus("Gateway message error")
                AgentStateStore.setError(message)
            }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            onMessage(webSocket, bytes.utf8())
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrentSocket(webSocket, attempt)) return
            val detail = "Gateway closed the connection (code $code${reason.takeIf { it.isNotBlank() }?.let { ": $it" } ?: ""})."
            handleConnectionFailure(attempt, webSocket, detail)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: okhttp3.Response?) {
            if (!isCurrentSocket(webSocket, attempt)) return
            val endpoint = currentEndpoint
            val message = if (endpoint == null) {
                "WebSocket connection failed: ${t.safeMessage()}"
            } else {
                formatFailure(endpoint, t, response)
            }
            handleConnectionFailure(attempt, webSocket, message)
        }
    }

    private fun handleEvent(root: JSONObject) {
        val payload = root.optJSONObject("payload") ?: return
        when (payload.optString("kind")) {
            "audio_out" -> {
                val data = payload.optString("data", "")
                if (data.isNotEmpty()) {
                    audioEngine?.enqueueAudio(data)
                } else {
                    audioEngine?.speak(payload.optString("text", ""))
                }
            }
            "speak" -> audioEngine?.speak(payload.optString("text", ""))
            "ui_event" -> {
                val name = payload.optString("name", "")
                val data = payload.optJSONObject("data") ?: JSONObject()
                when (name) {
                    "state" -> AgentStateStore.setAgentState(data.optString("state", "IDLE"))
                    "scanning" -> {
                        if (data.optBoolean("enabled", false)) {
                            AgentStateStore.setStatus(data.optString("text", "Scanning"))
                        }
                    }
                    "attention" -> {
                        val app = data.optString("app")
                        val title = data.optString("title")
                        AgentStateStore.addLog("Attention: $app $title".trim())
                    }
                    "phone_connected" -> AgentStateStore.setStatus("Connected")
                }
                onUiEvent?.invoke(name, data)
                if (name.isNotBlank()) AgentStateStore.addLog("Brahma UI: $name")
            }
            "screen_request" -> respondScreenFrame(payload.optString("request_id", ""))
        }
    }

    /**
     * The headless brain can ask for a screenshot. MediaProjection capture needs
     * an explicit user grant, so answer immediately (empty frame) instead of
     * letting the gateway wait for its timeout.
     */
    private fun respondScreenFrame(requestId: String) {
        val payload = JSONObject()
            .put("kind", "screen_frame")
            .put("request_id", requestId)
            .put("mime", "image/jpeg")
            .put("data", "")
        send(BrahmaProtocol.envelope(BrahmaProtocol.EVENT, payload))
    }

    fun sendEvent(payload: JSONObject) {
        send(BrahmaProtocol.envelope(BrahmaProtocol.EVENT, payload))
    }

    fun sendChatMessage(text: String) {
        audioEngine?.bargeIn()
        val payload = BrahmaProtocol.chatMessage(text)
        val msgId = payload.getString("request_id")
        val timestamp = System.currentTimeMillis()
        val pending = ChatMessage(msgId, "user", text, timestamp, "Sending...")
        AgentStateStore.addChatMessage(pending)
        val sent = send(payload)
        AgentStateStore.addChatMessage(pending.copy(status = if (sent) "Sent" else "Failed"))
    }
}
