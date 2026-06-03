@tool
extends EditorPlugin

## Godot Project Doctor — EditorPlugin entry point.
##
## Registers a "Project Doctor" panel at the bottom of the editor. The panel
## scans the currently open project (res://) for common issues with one click.
## A native GDScript port of the `gdoctor` CLI (github.com/chamcat97/godot-project-doctor).

const DoctorDock := preload("res://addons/godot_project_doctor/doctor_dock.gd")

var _dock: Control = null
var _bottom_button: Button = null


func _enter_tree() -> void:
	_dock = DoctorDock.new()
	# Inject the editor interface so result rows can open the relevant file.
	_dock.editor_interface = get_editor_interface()
	_bottom_button = add_control_to_bottom_panel(_dock, "🩺 Project Doctor")


func _exit_tree() -> void:
	if _dock != null:
		remove_control_from_bottom_panel(_dock)
		_dock.queue_free()
		_dock = null
	_bottom_button = null
