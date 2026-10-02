"""core/circuit_html.py — pure-python circuit schematic renderer.

Extracted from the old Qt ``core/circuit_hud.py`` so the circuit_assembler skill
keeps producing the same interactive HTML on Android, where the companion app
renders it instead of a PyQt window.  No Qt, no display, no PC dependencies.

Brahma AI Evo - Holographic Hardware Assembler & Interactive Circuit HUD.
Compact, elegant in-app popup overlay that visually shows pin-to-pin wiring
between microcontroller and sensors, matching the user's reference diagram.
"""

from __future__ import annotations

import json
import html as html_lib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.user_paths import get_user_data_dir

logger = logging.getLogger("CircuitHUD")

OUTPUT_DIR = get_user_data_dir() / "schematics"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def generate_circuit_html(circuit: Dict[str, Any]) -> str:
    """
    Generates a clean, compact, high-tech SVG circuit schematic HTML
    showing ONLY the title, component cards, neon wires, and legend.
    """
    title = html_lib.escape(str(circuit.get("title", "Connecting DHT11 and Arduino Pro Mini")))
    components = circuit.get("components", [])
    wires = circuit.get("wires", [])

    circuit_json = json.dumps(circuit).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  :root {{
    --bg-dark: #070c18;
    --card-bg: #0c1426;
    --card-hdr: #122849;
    --border-dim: rgba(56, 189, 248, 0.28);
    --border-cyan: #00f0ff;
    --text-white: #f8fafc;
    --text-dim: #94a3b8;
    --accent-cyan: #00f0ff;
    --accent-orange: #f97316;
    --accent-gold: #fbbf24;
    --accent-green: #22c55e;
  }}

  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    user-select: none;
  }}

  body {{
    background-color: var(--bg-dark);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    color: var(--text-white);
    padding: 22px 30px;
    height: 100vh;
    overflow: hidden;
    position: relative;
  }}

  /* Title Header */
  .stage-header {{
    margin-bottom: 24px;
    position: relative;
    z-index: 20;
  }}

  .stage-title {{
    font-size: 19px;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: 0.3px;
  }}

  .stage-title-bar {{
    width: 44px;
    height: 3px;
    background: #00f0ff;
    box-shadow: 0 0 8px #00f0ff;
    margin-top: 5px;
    border-radius: 2px;
  }}

  /* SVG Wiring Layer */
  svg#wires-layer {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    z-index: 5;
    pointer-events: none;
  }}

  .wire-path {{
    fill: none;
    stroke-width: 3.5;
    stroke-linecap: round;
    filter: drop-shadow(0 0 6px currentColor);
  }}

  /* Components Container */
  .components-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: relative;
    z-index: 10;
    margin-top: 10px;
  }}

  .hw-card {{
    background: var(--card-bg);
    border: 1.5px solid var(--border-dim);
    border-radius: 10px;
    width: 190px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.7);
    transition: transform 0.2s, box-shadow 0.2s;
  }}

  .hw-card.highlighted-card {{
    border: 2px solid #00f0ff !important;
    box-shadow: 0 0 18px rgba(0, 240, 255, 0.5), inset 0 0 10px rgba(0, 240, 255, 0.12) !important;
  }}

  .hw-card-header {{
    background: var(--card-hdr);
    border-bottom: 1px solid rgba(56, 189, 248, 0.2);
    padding: 8px 12px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
  }}

  .hw-card-header h3 {{
    font-size: 12px;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: 0.2px;
  }}

  .hw-card-header span {{
    font-size: 10px;
    color: var(--text-dim);
    display: block;
    margin-top: 1px;
  }}

  .hw-card-body {{
    display: flex;
    justify-content: space-between;
    padding: 12px 10px;
    min-height: 98px;
  }}

  .pins-col {{
    display: flex;
    flex-direction: column;
    gap: 12px;
  }}

  .pins-col.right {{
    align-items: flex-end;
    margin-left: auto;
  }}

  .pin-node {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    font-family: 'Consolas', monospace;
    font-weight: 600;
    color: #cbd5e1;
    position: relative;
  }}

  .pin-badge {{
    width: 20px;
    height: 20px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 800;
    color: #070c18;
    border: 1.5px solid rgba(255, 255, 255, 0.9);
  }}

  .pin-terminal {{
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #ffffff;
    box-shadow: 0 0 5px #ffffff;
  }}

  /* Legend */
  .stage-legend {{
    position: absolute;
    bottom: 20px;
    left: 30px;
    display: flex;
    gap: 20px;
    font-size: 11px;
    font-weight: 600;
    color: var(--text-dim);
    z-index: 20;
  }}

  .legend-item {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  .legend-line {{
    width: 18px;
    height: 3px;
    border-radius: 2px;
  }}
</style>
</head>
<body>

  <!-- Title -->
  <div class="stage-header">
    <div class="stage-title">{title}</div>
    <div class="stage-title-bar"></div>
  </div>

  <!-- SVG Wiring Layer -->
  <svg id="wires-layer">
    <defs>
      <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="3" result="blur" />
        <feMerge>
          <feMergeNode in="blur" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
    </defs>
  </svg>

  <!-- Component Cards Row -->
  <div class="components-row" id="components-row"></div>

  <!-- Legend -->
  <div class="stage-legend">
    <div class="legend-item">
      <div class="legend-line" style="background: #f97316; box-shadow: 0 0 6px #f97316;"></div>
      <span>Power</span>
    </div>
    <div class="legend-item">
      <div class="legend-line" style="background: #ffffff; box-shadow: 0 0 6px #ffffff;"></div>
      <span>Ground</span>
    </div>
  </div>

  <script>
    const circuitData = {circuit_json};

    const compRow = document.getElementById('components-row');
    const svgLayer = document.getElementById('wires-layer');

    function escapeHtml(value) {{
      return String(value ?? '').replace(/[&<>"']/g, char => ({{
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }})[char]);
    }}

    function safeColor(value) {{
      const color = String(value || '');
      return /^#[0-9a-fA-F]{{3,8}}$/.test(color) ? color : '#38bdf8';
    }}

    function renderComponents() {{
      compRow.innerHTML = '';
      circuitData.components.forEach(comp => {{
        const card = document.createElement('div');
        card.className = 'hw-card' + (comp.highlight ? ' highlighted-card' : '');
        card.id = `comp-${{escapeHtml(comp.id)}}`;

        let leftHtml = (comp.left_pins || []).map(p => `
          <div class="pin-node" id="pin-${{escapeHtml(comp.id)}}-left-${{escapeHtml(p.name)}}">
            ${{p.badge ? `<div class="pin-badge" style="background: ${{safeColor(p.color)}}; box-shadow: 0 0 8px ${{safeColor(p.color)}};">${{escapeHtml(p.badge)}}</div>` : `<div class="pin-terminal" style="background: ${{safeColor(p.color)}};"></div>`}}
            <span>${{escapeHtml(p.name)}}</span>
          </div>
        `).join('');

        let rightHtml = (comp.right_pins || []).map(p => `
          <div class="pin-node" id="pin-${{escapeHtml(comp.id)}}-right-${{escapeHtml(p.name)}}">
            <span>${{escapeHtml(p.name)}}</span>
            ${{p.badge ? `<div class="pin-badge" style="background: ${{safeColor(p.color)}}; box-shadow: 0 0 8px ${{safeColor(p.color)}};">${{escapeHtml(p.badge)}}</div>` : `<div class="pin-terminal" style="background: ${{safeColor(p.color)}};"></div>`}}
          </div>
        `).join('');

        card.innerHTML = `
          <div class="hw-card-header">
            <h3>${{escapeHtml(comp.name)}}</h3>
            <span>${{escapeHtml(comp.subtitle || '')}}</span>
          </div>
          <div class="hw-card-body">
            <div class="pins-col left">${{leftHtml}}</div>
            <div class="pins-col right">${{rightHtml}}</div>
          </div>
        `;
        compRow.appendChild(card);
      }});

      // Draw wires once DOM settles
      setTimeout(renderWires, 80);
    }}

    function findPinElement(comp, side, pin) {{
      if (side) {{
        const el = document.getElementById(`pin-${{comp}}-${{side}}-${{pin}}`);
        if (el) return el;
      }}
      return document.getElementById(`pin-${{comp}}-right-${{pin}}`)
          || document.getElementById(`pin-${{comp}}-left-${{pin}}`)
          || document.getElementById(`pin-${{comp}}-${{pin}}`);
    }}

    function renderWires() {{
      const defs = svgLayer.querySelector('defs');
      svgLayer.innerHTML = '';
      if (defs) svgLayer.appendChild(defs);

      const stageRect = document.body.getBoundingClientRect();

      circuitData.wires.forEach((w, idx) => {{
        const fromParts = w.from.split(':');
        const toParts = w.to.split(':');

        const fromComp = fromParts[0];
        const fromSide = fromParts.length === 3 ? fromParts[1] : null;
        const fromPin = fromParts[fromParts.length - 1];

        const toComp = toParts[0];
        const toSide = toParts.length === 3 ? toParts[1] : null;
        const toPin = toParts[toParts.length - 1];

        const elFrom = findPinElement(fromComp, fromSide, fromPin);
        const elTo = findPinElement(toComp, toSide, toPin);

        if (elFrom && elTo) {{
          const targetFrom = elFrom.querySelector('.pin-badge') || elFrom.querySelector('.pin-terminal') || elFrom;
          const targetTo = elTo.querySelector('.pin-badge') || elTo.querySelector('.pin-terminal') || elTo;

          const bFrom = targetFrom.getBoundingClientRect();
          const bTo = targetTo.getBoundingClientRect();

          const x1 = bFrom.left + bFrom.width / 2 - stageRect.left;
          const y1 = bFrom.top + bFrom.height / 2 - stageRect.top;
          const x2 = bTo.left + bTo.width / 2 - stageRect.left;
          const y2 = bTo.top + bTo.height / 2 - stageRect.top;

          // Organic Bezier curve
          const dx = Math.max(30, Math.abs(x2 - x1) * 0.5);
          const pathD = `M ${{x1}} ${{y1}} C ${{x1 + dx}} ${{y1}}, ${{x2 - dx}} ${{y2}}, ${{x2}} ${{y2}}`;

          const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          path.setAttribute('d', pathD);
          path.setAttribute('class', 'wire-path');
          path.setAttribute('id', `wire-${{idx}}`);
          path.setAttribute('stroke', safeColor(w.color));
          path.style.color = safeColor(w.color);

          // Flowing electron pulse dot
          const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          circle.setAttribute('r', '3.5');
          circle.setAttribute('fill', '#ffffff');
          circle.style.filter = 'drop-shadow(0 0 6px #ffffff)';

          const anim = document.createElementNS('http://www.w3.org/2000/svg', 'animateMotion');
          anim.setAttribute('path', pathD);
          anim.setAttribute('dur', `${{1.6 + idx * 0.25}}s`);
          anim.setAttribute('repeatCount', 'indefinite');

          circle.appendChild(anim);

          svgLayer.appendChild(path);
          svgLayer.appendChild(circle);
        }}
      }});
    }}

    window.addEventListener('resize', () => {{
      setTimeout(renderWires, 60);
    }});

    // Initialize
    renderComponents();
  </script>
</body>
</html>
"""
    return html


# =============================================================================
# IN-APP POPUP OVERLAY WIDGET (No separate window or taskbar item)
# =============================================================================
