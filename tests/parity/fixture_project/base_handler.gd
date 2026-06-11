class_name BaseHandler
extends Node

# Defines the handler that child_handler.gd inherits. The connection in
# inherit.tscn targeting _on_inherited must NOT be flagged (chain-walked).


func _on_inherited() -> void:
	pass
