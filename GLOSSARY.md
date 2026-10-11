# SidePulse Omarchy plugin

The language of the Omarchy bar widget that shows AI coding agents' status and
drives SidePulse devices, the way the macOS SidePulse menu-bar app does.

## Names

**SidePulse**:
The upstream project and product family, always written exactly so in prose
and UI. Lowercase `sidepulse` appears only inside identifiers: the package, the
CLI, the service, paths, the repo slug and the plugin id.
_Avoid_: Sidepulse, Side Pulse, sidepulse (in prose)

## Agents and sessions

**Agent**:
An AI coding CLI that reports to SidePulse through hooks (Claude Code, Codex,
Grok, Cursor, Junie).
_Avoid_: Provider (upstream's code term), tool, bot

**Session**:
One conversation of an agent, identified by the agent and its session id; one
row in the popup.
_Avoid_: Agent (for a single conversation), thread, run

**Subagent**:
A helper conversation an agent starts inside a session. It never gets its own
row: its most urgent state shows on its session.
_Avoid_: Child session, task

**State**:
What the user sees for a session or for everything at once: **Working**,
**Ask**, **Done** or **Idle**.
_Avoid_: Mode (upstream's finer-grained internal value that a State summarises), status

## Devices and LEDs

**Device**:
A physical SidePulse unit plugged into the computer: a **SidePulse Dot** or a
**SidePulse Pro**.
_Avoid_: Drive, card, dongle

**SidePulse Dot**:
The small SidePulse product with 2 LEDs.
_Avoid_: PulseDot (its drive label)

**SidePulse Pro**:
The SidePulse product with 8 LEDs; preferred over a Dot when both are plugged in.
_Avoid_: Card

**Bar LEDs**:
The row of virtual LEDs drawn along the edge of the Omarchy bar, mirroring
what a device shows; the Omarchy counterpart of upstream's SidePulse Notch.
_Avoid_: Notch (for ours), band, virtual device, on-screen LEDs

**Manual**:
A device the agent State does not drive, so something else decides what it
shows.
_Avoid_: Custom (upstream's setting value for it), disconnected, off

**Display**:
What a device's LEDs show, chosen per device: Agent (the agent State),
Battery (the battery level) or Off (dark). Battery and Off make the device
Manual.
_Avoid_: LED display, mode, led_display (upstream's setting name), Custom
