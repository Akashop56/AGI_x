import os
from pathlib import Path
import pytest

def test_web_background_assets_all_local():
    base_dir = Path(__file__).resolve().parent.parent
    bg_dir = base_dir / "assets" / "web_background"
    html_file = bg_dir / "index.html"

    assert html_file.exists(), "assets/web_background/index.html must exist"

    required_scripts = [
        "three.min.js",
        "OrbitControls.js",
        "EffectComposer.js",
        "RenderPass.js",
        "ShaderPass.js",
        "CopyShader.js",
        "LuminosityHighPassShader.js",
        "UnrealBloomPass.js",
    ]

    for script in required_scripts:
        script_path = bg_dir / script
        assert script_path.exists(), f"{script} must exist locally in assets/web_background"
        assert script_path.stat().st_size > 100, f"{script} must not be empty"

def test_web_background_instant_boot_no_document_write():
    base_dir = Path(__file__).resolve().parent.parent
    html_file = base_dir / "assets" / "web_background" / "index.html"
    content = html_file.read_text(encoding="utf-8")

    # Verify no blocking document.write calls
    assert "document.write" not in content, "index.html should not contain blocking document.write"
    # Verify no external CDN fallbacks that cause stalls when offline
    assert "cdnjs.cloudflare.com" not in content
    assert "cdn.jsdelivr.net" not in content

    # Verify robust startup boot function
    assert "function boot()" in content
    assert "document.readyState" in content

    # Verify safe canvas sizing
    assert "Math.max" in content

def test_opengl_context_sharing_flag():
    from PyQt6.QtCore import QCoreApplication, Qt
    assert QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
