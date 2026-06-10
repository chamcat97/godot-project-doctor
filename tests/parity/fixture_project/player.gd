extends UsedClass

# MISSING_EXT_RESOURCE (gdscript kind): static load() of a path that does not exist.
var broken_res = load("res://missing_thing.tres")


func _ready() -> void:
	# "jump" is declared in project.godot [input] -> no finding.
	if Input.is_action_pressed("jump"):
		UsedSingleton.poke()
	# "ghost_action" is NOT declared -> UNDEFINED_INPUT_ACTION.
	if Input.is_action_pressed("ghost_action"):
		queue_free()


# Target of the good [connection] in main_ok.tscn.
func _on_present() -> void:
	pass
