import QtQuick
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "c4pt0r.vortex"

  readonly property var modes: ["ocean", "monsoon", "cyclone", "jupiter", "smoke", "river", "shear",
                                "turbulence", "taichi", "solution", "whirlpool", "nebula", "matrix"]
  readonly property string launcher: decodeURIComponent(Qt.resolvedUrl("vortex-launch").toString().replace(/^file:\/\//, ""))
  readonly property var palettes: ["psychedelic", "acid", "sunset", "aurora", "deepsea", "phosphor"]
  readonly property string mode: setting("mode", "cyclone")

  function shellQuote(value) {
    return "'" + String(value).replace(/'/g, "'\\''") + "'"
  }

  function launch(chosenMode) {
    if (modes.indexOf(chosenMode) < 0) chosenMode = modes[Math.floor(Math.random() * modes.length)]
    var count = Math.max(1, Math.min(9, Math.round(Number(setting("count", 3))) || 3))
    var speed = Math.max(0.1, Math.min(3, Number(setting("speed", "0.7")) || 0.7))
    var args = [launcher, "--mode", chosenMode, "--count", count, "--speed", speed]
    var color = setting("color", "off")
    if (palettes.indexOf(color) >= 0) args.push("--color", color)
    if (setting("fullscreen", false)) args.push("--fullscreen")
    if (!setting("hud", false)) args.push("--no-hud")
    if (setting("mono", false)) args.push("--mono")
    if (root.bar) root.bar.run(args.map(shellQuote).join(" "))
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰢘"
    tooltipText: "Vortex: " + root.mode + " (right click: random, H inside: help)"
    horizontalMargin: 8.75

    onPressed: function(b) {
      root.launch(b === Qt.RightButton ? "random" : root.mode)
    }
  }
}
