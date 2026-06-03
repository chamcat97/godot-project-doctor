@tool
extends RefCounted

## Indexer for the currently open project (res://).
##
## Native GDScript port of the CLI's scanner.py + parser.py + indexer.py +
## gdscript.py + the uid-pair collection from uid_map.py. Produces a single
## `index` Dictionary consumed by checks.gd.
##
## File-list entries and `source_file` fields are project-relative POSIX paths
## (e.g. "demo/unused.gd"); ref `path` fields keep their raw form (res://,
## uid://, or relative) exactly as written in the source file.

# Directories skipped entirely during the walk (kept in sync with indexer.py).
const SKIP_DIRS := [
	".git", ".godot", ".import", "__pycache__", "build",
	".gradle", "node_modules", ".venv", "venv", "dist", ".cache",
]

# Compiled regexes (built once in _init).
var _ext_res_re: RegEx
var _attr_re: RegEx
var _static_load_re: RegEx
var _input_action_re: RegEx


func _init() -> void:
	_ext_res_re = RegEx.new()
	_ext_res_re.compile("\\[ext_resource\\b([^\\]]*)\\]")

	_attr_re = RegEx.new()
	_attr_re.compile("\\b(\\w+)=\"([^\"]*)\"")

	_static_load_re = RegEx.new()
	_static_load_re.compile(
		"(?<![A-Za-z0-9_])(?<call>ResourceLoader\\.load|preload|load)(?![A-Za-z0-9_])" +
		"\\s*\\(\\s*(?:\"(?<path_dq>res://[^\"]*)\"|'(?<path_sq>res://[^']*)')\\s*"
	)

	_input_action_re = RegEx.new()
	_input_action_re.compile(
		"(?<![A-Za-z0-9_])Input\\.(?:is_action(?:_pressed|_just_pressed|_just_released)?|" +
		"get_action_(?:strength|raw_strength)|action_(?:press|release))" +
		"\\s*\\(\\s*(?:\"(?<name_dq>[^\"]+)\"|'(?<name_sq>[^']+)')"
	)


# Public entry point

## Scan res:// and return the populated index Dictionary.
##
## res://addons/ and res://script_templates/ are skipped entirely — the doctor
## audits your project's code, not third-party plugins or editor tooling.
func scan() -> Dictionary:
	var index := {
		"project_name": "",
		"main_scene": "",
		"icon": "",
		"godot_version": str(Engine.get_version_info().get("string", "")),
		"autoloads": {},          # name -> res:// path
		"input_actions": [],      # declared action names (user-defined)
		"scenes": [],
		"resources": [],
		"scripts": [],
		"shaders": [],
		"images": [],
		"audio": [],
		"other": [],
		"has_export_presets": false,
		"refs": [],               # Array of ref dicts
		"input_action_refs": [],  # Array of {name, source_file}
		"uid_pairs": [],          # Array of {uid, path, source} for DUPLICATE_UID
	}

	_read_project_settings(index)
	_walk("", index)

	index.scenes.sort()
	index.resources.sort()
	index.scripts.sort()
	index.shaders.sort()
	index.images.sort()
	index.audio.sort()
	index.other.sort()
	return index


# Project settings (replaces parser.py via ProjectSettings)

func _read_project_settings(index: Dictionary) -> void:
	index.project_name = str(ProjectSettings.get_setting("application/config/name", ""))
	index.main_scene = str(ProjectSettings.get_setting("application/run/main_scene", ""))
	index.icon = str(ProjectSettings.get_setting("application/config/icon", ""))

	for prop in ProjectSettings.get_property_list():
		var n: String = str(prop.get("name", ""))
		if n.begins_with("autoload/"):
			var auto_name := n.substr("autoload/".length())
			var val := str(ProjectSettings.get_setting(n, ""))
			if val.begins_with("*"):
				val = val.substr(1)
			if val != "":
				index.autoloads[auto_name] = val
		elif n.begins_with("input/"):
			var action := n.substr("input/".length())
			if action != "" and not index.input_actions.has(action):
				index.input_actions.append(action)


# Filesystem walk + per-file indexing

func _walk(rel_dir: String, index: Dictionary) -> void:
	var abs_dir := "res://" + rel_dir
	var dir := DirAccess.open(abs_dir)
	if dir == null:
		return

	dir.list_dir_begin()
	var subdirs: Array[String] = []
	var files: Array[String] = []
	var name := dir.get_next()
	while name != "":
		if name != "." and name != "..":
			if dir.current_is_dir():
				subdirs.append(name)
			else:
				files.append(name)
		name = dir.get_next()
	dir.list_dir_end()

	files.sort()
	for f in files:
		_index_file(rel_dir, f, index)

	subdirs.sort()
	for d in subdirs:
		if SKIP_DIRS.has(d):
			continue
		# Third-party plugins and editor script templates are out of scope.
		if rel_dir == "" and (d == "addons" or d == "script_templates"):
			continue
		var child := d if rel_dir == "" else rel_dir + "/" + d
		_walk(child, index)


func _index_file(rel_dir: String, fname: String, index: Dictionary) -> void:
	if fname == "project.godot":
		return
	if fname == "export_presets.cfg":
		index.has_export_presets = true
		return

	var rel := fname if rel_dir == "" else rel_dir + "/" + fname
	var ext := fname.get_extension().to_lower()

	match ext:
		"tscn":
			index.scenes.append(rel)
			_append_array(index.refs, _parse_ext_resources(rel))
		"tres":
			index.resources.append(rel)
			_append_array(index.refs, _parse_ext_resources(rel))
		"gd":
			index.scripts.append(rel)
			_append_array(index.refs, _extract_gdscript_refs(rel))
			_append_array(index.input_action_refs, _extract_input_action_refs(rel))
		"shader", "gdshader":
			index.shaders.append(rel)
		"png", "jpg", "jpeg", "webp", "svg":
			index.images.append(rel)
		"wav", "ogg", "mp3":
			index.audio.append(rel)
		"uid":
			_collect_uid_sidecar(rel, index.uid_pairs)
		"import":
			_collect_uid_import(rel, index.uid_pairs)
		_:
			index.other.append(rel)


# ext_resource parsing (.tscn / .tres)

func _parse_ext_resources(rel: String) -> Array:
	var out: Array = []
	var text := read_text(rel)
	if text == "":
		return out

	for bm in _ext_res_re.search_all(text):
		var attrs := _parse_attrs(bm.get_string(1))
		var path: String = attrs.get("path", "")
		if path == "":
			continue
		# Normalise non-res:// relative paths (rare; Godot normally writes res://).
		if not path.begins_with("res://") and not path.begins_with("uid://"):
			path = "res://" + normalize_posix(rel.get_base_dir() + "/" + path)
		out.append({
			"source_file": rel,
			"type": attrs.get("type", ""),
			"uid": attrs.get("uid", ""),
			"path": path,
			"id": attrs.get("id", ""),
			"kind": "ext_resource",
		})
	return out


func _parse_attrs(s: String) -> Dictionary:
	var attrs := {}
	for m in _attr_re.search_all(s):
		attrs[m.get_string(1)] = m.get_string(2)
	return attrs


# GDScript static refs + input-action refs (.gd)

func _extract_gdscript_refs(rel: String) -> Array:
	var out: Array = []
	var text := read_text(rel)
	if text == "":
		return out

	for raw_line in text.split("\n"):
		if raw_line.strip_edges(true, false).begins_with("#"):
			continue
		var line := strip_inline_comment(raw_line)
		for m in _static_load_re.search_all(line):
			if pos_in_string(line, m.get_start(0)):
				continue
			var path := m.get_string("path_dq")
			if path == "":
				path = m.get_string("path_sq")
			if path == "":
				continue
			out.append({
				"source_file": rel,
				"type": m.get_string("call"),
				"uid": "",
				"path": path,
				"id": "",
				"kind": "gdscript",
			})
	return out


func _extract_input_action_refs(rel: String) -> Array:
	var out: Array = []
	var text := read_text(rel)
	if text == "":
		return out

	for raw_line in text.split("\n"):
		if raw_line.strip_edges(true, false).begins_with("#"):
			continue
		var line := strip_inline_comment(raw_line)
		for m in _input_action_re.search_all(line):
			if pos_in_string(line, m.get_start(0)):
				continue
			var nm := m.get_string("name_dq")
			if nm == "":
				nm = m.get_string("name_sq")
			if nm != "":
				out.append({"name": nm, "source_file": rel})
	return out


# uid pair collection (for DUPLICATE_UID)

func _collect_uid_sidecar(rel: String, pairs: Array) -> void:
	# `scripts/player.gd.uid` -> uid maps to res://scripts/player.gd
	var text := read_text(rel).strip_edges()
	if not text.begins_with("uid://"):
		return
	var mapped := "res://" + rel.substr(0, rel.length() - 4)  # drop ".uid"
	pairs.append({"uid": text, "path": mapped, "source": "uid_sidecar"})


func _collect_uid_import(rel: String, pairs: Array) -> void:
	# `art/hero.png.import` [remap] uid="uid://..." -> res://art/hero.png
	var text := read_text(rel)
	if text == "":
		return
	var uid := ""
	for m in _attr_re.search_all(text):
		if m.get_string(1) == "uid":
			uid = m.get_string(2)
			break
	if not uid.begins_with("uid://"):
		return
	var mapped := "res://" + rel.substr(0, rel.length() - 7)  # drop ".import"
	pairs.append({"uid": uid, "path": mapped, "source": "import"})


# Shared static helpers (also used by checks.gd)

static func read_text(rel_or_res: String) -> String:
	var p := rel_or_res if rel_or_res.begins_with("res://") else "res://" + rel_or_res
	if not FileAccess.file_exists(p):
		return ""
	var t := FileAccess.get_file_as_string(p)
	return t.replace("\r\n", "\n").replace("\r", "\n")


static func normalize_posix(path: String) -> String:
	var parts := PackedStringArray()
	for comp in path.replace("\\", "/").split("/"):
		if comp == "..":
			if parts.size() > 0:
				parts.remove_at(parts.size() - 1)
		elif comp != "" and comp != ".":
			parts.append(comp)
	return "/".join(parts)


static func re_escape(s: String) -> String:
	var out := ""
	for i in s.length():
		var c := s[i]
		if (c >= "a" and c <= "z") or (c >= "A" and c <= "Z") or (c >= "0" and c <= "9") or c == "_":
			out += c
		else:
			out += "\\" + c
	return out


## Return *line* with everything from the first `#` outside a string removed.
static func strip_inline_comment(line: String) -> String:
	var in_str := false
	var q := ""
	var i := 0
	var n := line.length()
	while i < n:
		var c := line[i]
		if in_str:
			if c == "\\":
				i += 2
				continue
			if c == q:
				in_str = false
		else:
			if c == "\"" or c == "'":
				in_str = true
				q = c
			elif c == "#":
				return line.substr(0, i)
		i += 1
	return line


## Return true when the character at *pos* sits inside a string literal.
static func pos_in_string(line: String, pos: int) -> bool:
	var in_str := false
	var q := ""
	var i := 0
	while i < pos:
		var c := line[i]
		if in_str:
			if c == "\\":
				i += 2
				continue
			if c == q:
				in_str = false
		else:
			if c == "\"" or c == "'":
				in_str = true
				q = c
		i += 1
	return in_str


static func _append_array(dest: Array, src: Array) -> void:
	for item in src:
		dest.append(item)
