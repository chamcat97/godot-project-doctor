@tool
extends VBoxContainer

## Bottom-panel UI for Godot Project Doctor.
##
## One "Scan Project" button audits res:// and lists findings in a Tree grouped
## by severity. Severity filters and an "Include addons" toggle re-filter the
## results without rescanning. Activating a row opens the offending file.

const Scanner := preload("res://addons/godot_project_doctor/scanner.gd")
const Checks := preload("res://addons/godot_project_doctor/checks.gd")

const SEVERITIES := ["ERROR", "WARNING", "INFO"]

# Injected by plugin.gd before the dock enters the tree.
var editor_interface = null

var _scan_button: Button
var _summary_label: Label
var _cb_error: CheckBox
var _cb_warning: CheckBox
var _cb_info: CheckBox
var _cb_addons: CheckBox
var _tree: Tree

var _issues: Array = []
var _last_index: Dictionary = {}


func _ready() -> void:
	_build_ui()


func _build_ui() -> void:
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	custom_minimum_size = Vector2(0, 220)
	add_theme_constant_override("separation", 4)

	# Toolbar
	var bar := HBoxContainer.new()
	bar.add_theme_constant_override("separation", 8)
	add_child(bar)

	_scan_button = Button.new()
	_scan_button.text = "🩺 Scan Project"
	_scan_button.tooltip_text = "Audit the current project (res://) for common issues."
	_scan_button.pressed.connect(_on_scan_pressed)
	bar.add_child(_scan_button)

	bar.add_child(VSeparator.new())

	_cb_error = _make_filter(bar, "Errors")
	_cb_warning = _make_filter(bar, "Warnings")
	_cb_info = _make_filter(bar, "Info")

	bar.add_child(VSeparator.new())

	_cb_addons = CheckBox.new()
	_cb_addons.text = "Include addons"
	_cb_addons.button_pressed = false
	_cb_addons.tooltip_text = "Also audit code under res://addons/ and res://script_templates/."
	bar.add_child(_cb_addons)

	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bar.add_child(spacer)

	_summary_label = Label.new()
	_summary_label.text = "Click \"Scan Project\" to audit res://."
	_summary_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	bar.add_child(_summary_label)

	# Results tree
	_tree = Tree.new()
	_tree.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_tree.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_tree.columns = 3
	_tree.column_titles_visible = true
	_tree.set_column_title(0, "Issue")
	_tree.set_column_title(1, "Message")
	_tree.set_column_title(2, "File")
	_tree.set_column_expand(0, false)
	_tree.set_column_custom_minimum_width(0, 230)
	_tree.set_column_expand(1, true)
	_tree.set_column_expand(2, false)
	_tree.set_column_custom_minimum_width(2, 260)
	_tree.hide_root = true
	_tree.item_activated.connect(_on_item_activated)
	add_child(_tree)


func _make_filter(parent: Node, label: String) -> CheckBox:
	var cb := CheckBox.new()
	cb.text = label
	cb.button_pressed = true
	cb.toggled.connect(func(_pressed): _populate())
	parent.add_child(cb)
	return cb


# Scan

func _on_scan_pressed() -> void:
	_scan_button.disabled = true
	_summary_label.text = "Scanning…"
	# Let the label repaint before the (synchronous) scan blocks the UI.
	await get_tree().process_frame

	var scanner := Scanner.new()
	_last_index = scanner.scan()
	var checks := Checks.new()
	_issues = checks.run_all(_last_index, _cb_addons.button_pressed)

	_update_summary()
	_populate()
	_scan_button.disabled = false


func _update_summary() -> void:
	var counts := {"ERROR": 0, "WARNING": 0, "INFO": 0}
	for it in _issues:
		counts[it.severity] += 1
	_summary_label.text = "%d errors, %d warnings, %d info  ·  scanned %d scenes, %d scripts" % [
		counts.ERROR, counts.WARNING, counts.INFO,
		_last_index.get("scenes", []).size(), _last_index.get("scripts", []).size(),
	]


# Results tree population

func _populate() -> void:
	_tree.clear()
	var root := _tree.create_item()

	var groups := {"ERROR": [], "WARNING": [], "INFO": []}
	for it in _issues:
		if _severity_enabled(it.severity):
			groups[it.severity].append(it)

	var shown := 0
	for sev in SEVERITIES:
		var arr: Array = groups[sev]
		if arr.is_empty():
			continue
		var g := _tree.create_item(root)
		g.set_text(0, "%s (%d)" % [sev, arr.size()])
		g.set_selectable(0, false)
		g.set_selectable(1, false)
		g.set_selectable(2, false)
		var color := _sev_color(sev)
		g.set_custom_color(0, color)
		var icon := _sev_icon(sev)
		if icon != null:
			g.set_icon(0, icon)
		for it in arr:
			var row := _tree.create_item(g)
			row.set_text(0, it.code)
			row.set_custom_color(0, color)
			if icon != null:
				row.set_icon(0, icon)
			row.set_text(1, it.message)
			row.set_text(2, it.file)
			row.set_metadata(0, it)
			var tip: String = it.code + "\n\n" + it.message
			if it.details != "":
				tip += "\n\n" + it.details
			row.set_tooltip_text(0, tip)
			row.set_tooltip_text(1, tip)
			row.set_tooltip_text(2, tip)
			shown += 1

	if shown == 0:
		var empty := _tree.create_item(root)
		if _issues.is_empty():
			empty.set_text(0, "No issues found 🎉")
		else:
			empty.set_text(0, "No issues match the current filters.")
		empty.set_selectable(0, false)


func _severity_enabled(sev: String) -> bool:
	match sev:
		"ERROR":
			return _cb_error.button_pressed
		"WARNING":
			return _cb_warning.button_pressed
		"INFO":
			return _cb_info.button_pressed
	return true


# Open the file behind an activated row

func _on_item_activated() -> void:
	var item := _tree.get_selected()
	if item == null:
		return
	var it = item.get_metadata(0)
	if it == null or typeof(it) != TYPE_DICTIONARY:
		return
	var f: String = it.get("file", "")
	if f == "" or f == "project.godot":
		return  # nothing navigable (e.g. circular-dependency / project-level findings)
	_open_file(f)


func _open_file(rel: String) -> void:
	if editor_interface == null:
		return
	var res := "res://" + rel
	if not FileAccess.file_exists(res):
		return
	var ext := rel.get_extension().to_lower()
	if ext == "tscn":
		editor_interface.open_scene_from_path(res)
	elif ext == "gd":
		var scr = load(res)
		if scr != null:
			editor_interface.edit_script(scr)
			editor_interface.set_main_screen_editor("Script")
	else:
		var fsd = editor_interface.get_file_system_dock()
		if fsd != null:
			fsd.navigate_to_path(res)


# Severity styling (editor theme, with graceful fallbacks)

func _sev_color(sev: String) -> Color:
	var key := "success_color"
	var fallback := Color(0.55, 0.80, 0.55)
	if sev == "ERROR":
		key = "error_color"
		fallback = Color(0.93, 0.36, 0.36)
	elif sev == "WARNING":
		key = "warning_color"
		fallback = Color(0.93, 0.76, 0.30)
	if has_theme_color(key, "Editor"):
		return get_theme_color(key, "Editor")
	return fallback


func _sev_icon(sev: String) -> Texture2D:
	var candidates: Array = ["NodeInfo", "Info", "Popup"]
	if sev == "ERROR":
		candidates = ["StatusError", "Error"]
	elif sev == "WARNING":
		candidates = ["StatusWarning", "NodeWarning", "Warning"]
	for icon_name in candidates:
		if has_theme_icon(icon_name, "EditorIcons"):
			return get_theme_icon(icon_name, "EditorIcons")
	return null
