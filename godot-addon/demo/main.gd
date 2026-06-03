extends Node

# Demo script attached to the main scene (referenced -> NOT flagged as unused).
# It references an input action "jump" that is NOT defined in the project's
# Input Map, so the doctor reports UNDEFINED_INPUT_ACTION.
func _process(_delta: float) -> void:
	if Input.is_action_just_pressed("jump"):
		print("jump pressed")
