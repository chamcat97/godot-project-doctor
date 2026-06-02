@tool
extends RefCounted

## Audit checks for a scanned project index.
##
## Native GDScript port of the CLI's checks.py + graph.py (cycle detection) +
## the DUPLICATE_UID rule from uid_map.py. `run_all(index)` returns an Array of
## issue Dictionaries: {code, severity, message, file, details}.

const Scanner := preload("res://addons/godot_project_doctor/scanner.gd")

const LARGE_TEXTURE_DIM := 2048
const LARGE_AUDIO_BYTES := 10 * 1024 * 1024
const BUILTIN_ACTION_PREFIX := "ui_"
const RASTER_EXTS := ["png", "jpg", "jpeg", "webp"]
const BINARY_REF_EXTS := ["res", "scn"]

# Scene-connection parsing regexes (BROKEN_SIGNAL_CONNECTION).
var _sc_ext_res_re: RegEx
var _sc_node_re: RegEx
var _sc_conn_re: RegEx
var _sc_attr_re: RegEx
var _sc_script_prop_re: RegEx


func _init() -> void:
	_sc_ext_res_re = RegEx.new()
	_sc_ext_res_re.compile("^\\[ext_resource\\b([^\\]]*)\\]")
	_sc_node_re = RegEx.new()
	_sc_node_re.compile("^\\[node\\b([^\\]]*)\\]")
	_sc_conn_re = RegEx.new()
	_sc_conn_re.compile("^\\[connection\\b([^\\]]*)\\]")
	_sc_attr_re = RegEx.new()
	_sc_attr_re.compile("\\b(\\w+)=\"([^\"]*)\"")
	_sc_script_prop_re = RegEx.new()
	_sc_script_prop_re.compile("^script\\s*=\\s*ExtResource\\(\\s*\"?([^\"\\)\\s]+)\"?\\s*\\)")


func run_all(index: Dictionary, include_addons: bool = false) -> Array:
	var issues: Array = []
	_append(issues, _check_missing_export_presets(index))
	_append(issues, _check_project_integrity(index))
	_append(issues, _check_circular(index))
	_append(issues, _check_missing_ext(index))
	_append(issues, _check_large_textures(index))
	_append(issues, _check_large_audio(index))
	_append(issues, _check_unused_assets(index))
	_append(issues, _check_undefined_inputs(index))
	_append(issues, _check_broken_signals(index))
	_append(issues, _check_unused_scripts(index))
	_append(issues, _check_unused_autoloads(index))
	_append(issues, _check_duplicate_uid(index))

	# Suppress findings *located in* addons/ & script_templates/ by default
	# (their code is referenced via class_name / the editor, producing noise).
	# Addon files are still indexed above, so their cross-references count.
	if include_addons:
		return issues
	var filtered: Array = []
	for it in issues:
		if not _under_addons(it["file"]):
			filtered.append(it)
	return filtered


# Individual checks

func _check_missing_export_presets(index: Dictionary) -> Array:
	if index.has_export_presets:
		return []
	return [_issue(
		"NO_EXPORT_PRESETS", "INFO",
		"export_presets.cfg not found. No export configuration is present.",
		"",
		"Create export presets in the Godot editor if you intend to export the project.",
	)]


func _check_project_integrity(index: Dictionary) -> Array:
	var out: Array = []
	var main: String = index.main_scene

	if main == "":
		out.append(_issue(
			"NO_MAIN_SCENE", "INFO",
			"No main scene configured in project.godot.",
			"project.godot",
			"Set run/main_scene in project.godot if this is a runnable game. "
			+ "Library-only projects may intentionally omit a main scene.",
		))
	elif _missing_target(main):
		out.append(_issue(
			"MISSING_MAIN_SCENE", "ERROR",
			"Main scene does not exist: " + main,
			"project.godot",
			"Expected at: " + _resolve_ref_res(main, ""),
		))

	var names: Array = index.autoloads.keys()
	names.sort()
	for nm in names:
		var p: String = index.autoloads[nm]
		if _missing_target(p):
			out.append(_issue(
				"MISSING_AUTOLOAD", "ERROR",
				"Autoload '%s' path does not exist: %s" % [nm, p],
				"project.godot",
				"Expected at: " + _resolve_ref_res(p, ""),
			))
	return out


func _check_circular(index: Dictionary) -> Array:
	var graph := _build_graph(index)
	var cycles := _find_cycles(graph)
	var out: Array = []
	for cycle in cycles:
		var chain := _join_strings(cycle, " -> ") + " -> " + str(cycle[0])
		out.append(_issue(
			"CIRCULAR_DEPENDENCY", "ERROR",
			"Circular dependency detected (%d node(s)): %s" % [cycle.size(), cycle[0]],
			"",
			"Cycle: " + chain,
		))
	return out


func _check_missing_ext(index: Dictionary) -> Array:
	var out: Array = []
	for ref in index.refs:
		var res := _resolve_ref_res(ref.path, ref.source_file)
		if res == "":
			continue  # unresolvable uid:// -> skip to avoid false positives
		if not FileAccess.file_exists(res):
			out.append(_issue(
				"MISSING_EXT_RESOURCE", "ERROR",
				"External resource not found: " + ref.path,
				ref.source_file,
				"Referenced in [%s] as type='%s' id='%s'. Expected at: %s"
					% [ref.source_file, ref.type, ref.id, res],
			))
	return out


func _check_large_textures(index: Dictionary) -> Array:
	var out: Array = []
	for rel in index.images:
		if not RASTER_EXTS.has(String(rel).get_extension().to_lower()):
			continue
		# Use the imported texture (not Image.load_from_file, which spams an
		# "this will not work on export" warning for every imported asset).
		var tex = load("res://" + rel)
		if not (tex is Texture2D):
			continue
		var w: int = tex.get_width()
		var h: int = tex.get_height()
		if w > LARGE_TEXTURE_DIM or h > LARGE_TEXTURE_DIM:
			out.append(_issue(
				"LARGE_TEXTURE", "WARNING",
				"Large texture (%dx%d): %s" % [w, h, rel],
				rel,
				"Image dimensions %dx%d exceed the %dpx threshold. "
					% [w, h, LARGE_TEXTURE_DIM]
					+ "Consider downscaling or using mipmaps to reduce GPU memory usage.",
			))
	return out


func _check_large_audio(index: Dictionary) -> Array:
	var out: Array = []
	var limit_mb := float(LARGE_AUDIO_BYTES) / (1024.0 * 1024.0)
	for rel in index.audio:
		var f := FileAccess.open("res://" + rel, FileAccess.READ)
		if f == null:
			continue
		var size := f.get_length()
		f.close()
		if size > LARGE_AUDIO_BYTES:
			var mb := float(size) / (1024.0 * 1024.0)
			out.append(_issue(
				"LARGE_AUDIO", "WARNING",
				"Large audio file (%.1f MB): %s" % [mb, rel],
				rel,
				"File size %.1f MB exceeds the %d MB threshold. " % [mb, int(limit_mb)]
					+ "Consider compressing or streaming this asset.",
			))
	return out


func _check_unused_assets(index: Dictionary) -> Array:
	var referenced := {}
	for ref in index.refs:
		var canon := _ref_to_canonical_rel(ref.path, ref.source_file)
		if canon != "":
			referenced[canon.replace("\\", "/")] = true

	# The project icon is referenced even though it never appears in ext_resource.
	var icon: String = index.icon
	if icon.begins_with("res://"):
		referenced[icon.substr(6).replace("\\", "/")] = true

	var out: Array = []
	for rel in (index.images + index.audio):
		if not referenced.has(String(rel).replace("\\", "/")):
			out.append(_issue(
				"UNUSED_ASSET_CANDIDATE", "WARNING",
				"Asset not referenced by any parsed scene or resource: " + rel,
				rel,
				"This file was not found in any ext_resource declaration or static "
					+ "load()/preload() call. It may be loaded dynamically via GDScript, "
					+ "or it may be genuinely unused.",
			))
	return out


func _check_undefined_inputs(index: Dictionary) -> Array:
	if index.input_action_refs.is_empty():
		return []

	var by_action := {}
	for r in index.input_action_refs:
		var a: String = r.name
		if not by_action.has(a):
			by_action[a] = []
		by_action[a].append(r.source_file)

	var out: Array = []
	var names := by_action.keys()
	names.sort()
	for a in names:
		if a.begins_with(BUILTIN_ACTION_PREFIX):
			continue
		if index.input_actions.has(a):
			continue
		var files := _unique_sorted(by_action[a])
		var quoted: Array = []
		for f in files.slice(0, 3):
			quoted.append("'" + str(f) + "'")
		var file_list := _join_strings(quoted, ", ")
		if files.size() > 3:
			file_list += " ... (+%d more)" % (files.size() - 3)
		out.append(_issue(
			"UNDEFINED_INPUT_ACTION", "WARNING",
			"Input action '%s' used in GDScript but not declared in project.godot" % a,
			files[0],
			"Referenced in %d file(s): %s. " % [files.size(), file_list]
				+ "Add the action in the Godot editor Input Map settings, "
				+ "or remove the reference if the action is obsolete.",
		))
	return out


func _check_broken_signals(index: Dictionary) -> Array:
	var out: Array = []
	var func_cache := {}  # "res_path|method" -> bool (method present)

	for scene_rel in index.scenes:
		var text := Scanner.read_text(scene_rel)
		if text == "":
			continue
		var parsed := _parse_scene_connections(text)
		var connections: Array = parsed.connections
		if connections.is_empty():
			continue
		var id_to_path: Dictionary = parsed.id_to_path
		var node_scripts: Dictionary = parsed.node_scripts

		for conn in connections:
			var to_raw: String = conn.to
			var method: String = conn.method
			if method == "":
				continue
			var to_path := "" if to_raw == "." else to_raw
			if not node_scripts.has(to_path):
				continue
			var script_ref_id: String = node_scripts[to_path]
			if not id_to_path.has(script_ref_id):
				continue
			var script_res: String = id_to_path[script_ref_id]
			if script_res == "" or not script_res.begins_with("res://"):
				continue

			var cache_key := script_res + "|" + method
			var present: bool
			if func_cache.has(cache_key):
				present = func_cache[cache_key]
			else:
				present = _script_has_func(script_res, method)
				func_cache[cache_key] = present
			if present:
				continue

			out.append(_issue(
				"BROKEN_SIGNAL_CONNECTION", "WARNING",
				"Signal '%s' connection targets missing method '%s' (not found in '%s')"
					% [conn["signal"], method, script_res],
				scene_rel,
				"Connection: signal='%s' from='%s' to='%s' method='%s'. Script: %s. "
					% [conn["signal"], conn.get("from", "."), to_raw, method, script_res]
					+ "If '%s' is inherited from a base class, this is a false positive."
					% method,
			))
	return out


func _check_unused_scripts(index: Dictionary) -> Array:
	var referenced := {}
	for ref in index.refs:
		var canon := _ref_to_canonical_rel(ref.path, ref.source_file)
		if canon != "":
			var cn := canon.replace("\\", "/")
			if cn.ends_with(".gd"):
				referenced[cn] = true

	for nm in index.autoloads:
		var canon := _ref_to_canonical_rel(index.autoloads[nm], "")
		if canon != "" and canon.ends_with(".gd"):
			referenced[canon.replace("\\", "/")] = true

	var usage := _collect_class_name_usage(index)
	var script_to_class: Dictionary = usage.script_to_class
	var used: Dictionary = usage.used

	var out: Array = []
	for script in index.scripts:
		var norm := String(script).replace("\\", "/")
		if referenced.has(norm):
			continue
		if script_to_class.has(norm) and used.has(script_to_class[norm]):
			continue  # referenced via class_name (extends / type / node type)
		out.append(_issue(
			"UNUSED_SCRIPT", "WARNING",
			"Script not referenced by any scene, resource, or autoload: " + script,
			script,
			"This .gd file was not found in any ext_resource declaration, static "
				+ "load()/preload() call, autoload entry, or class_name reference. It may "
				+ "be a tool/editor script, loaded dynamically, or genuinely unused.",
		))
	return out


func _check_unused_autoloads(index: Dictionary) -> Array:
	if index.autoloads.is_empty():
		return []

	var pats := {}
	var found := {}
	for nm in index.autoloads:
		var re := RegEx.new()
		re.compile("\\b" + Scanner.re_escape(nm) + "\\b")
		pats[nm] = re
		found[nm] = false

	for script_rel in index.scripts:
		var t := Scanner.read_text(script_rel)
		if t == "":
			continue
		for nm in pats:
			if not found[nm] and pats[nm].search(t) != null:
				found[nm] = true
		if _all_true(found):
			break

	var out: Array = []
	var names: Array = index.autoloads.keys()
	names.sort()
	for nm in names:
		if found[nm]:
			continue
		out.append(_issue(
			"UNUSED_AUTOLOAD", "WARNING",
			"Autoload '%s' is declared but never referenced in any GDScript file" % nm,
			"project.godot",
			"'%s' is declared as an autoload (path: %s) but its name does not appear "
				% [nm, index.autoloads[nm]]
				+ "in any .gd file. It may be accessed via get_node('/root/...') or only "
				+ "from C#/GDExtension code.",
		))
	return out


func _check_duplicate_uid(index: Dictionary) -> Array:
	var by_uid := {}  # uid -> {path: source}
	for pr in index.uid_pairs:
		var u: String = pr.uid
		if not by_uid.has(u):
			by_uid[u] = {}
		by_uid[u][pr.path] = pr.source

	var out: Array = []
	var uids := by_uid.keys()
	uids.sort()
	for u in uids:
		var paths_map: Dictionary = by_uid[u]
		if paths_map.size() <= 1:
			continue
		var paths := paths_map.keys()
		paths.sort()
		out.append(_issue(
			"DUPLICATE_UID", "WARNING",
			"Duplicate UID %s claimed by %d resources" % [u, paths.size()],
			paths[0],
			"UID %s is assigned to multiple paths: %s" % [u, _join_strings(paths, ", ")],
		))
	return out


# Scene connection parsing (port of _parse_scene_for_connections)

func _parse_scene_connections(text: String) -> Dictionary:
	var id_to_path := {}      # ref_id -> script res path (Script-type ext_resources)
	var node_scripts := {}    # node_path -> ref_id
	var connections: Array = []
	var in_node := false
	var current_node_path := ""

	for line in text.split("\n"):
		var s := line.strip_edges()
		if s == "":
			continue

		if s.begins_with("["):
			var m := _sc_ext_res_re.search(s)
			if m != null:
				var attrs := _parse_attrs(m.get_string(1))
				var rid: String = attrs.get("id", "")
				var rtype: String = attrs.get("type", "")
				var rpath: String = attrs.get("path", "")
				if rid != "" and rpath != "" and (rtype == "Script" or rtype == "GDScript" or rtype == "CSharpScript"):
					if rpath.begins_with("uid://"):
						var resolved := _resolve_ref_res(rpath, "")
						if resolved != "":
							rpath = resolved
					id_to_path[rid] = rpath
				in_node = false
				continue

			m = _sc_node_re.search(s)
			if m != null:
				var attrs := _parse_attrs(m.get_string(1))
				var nm: String = attrs.get("name", "")
				if not attrs.has("parent"):
					current_node_path = ""  # root node
				elif attrs["parent"] == ".":
					current_node_path = nm
				else:
					current_node_path = str(attrs["parent"]) + "/" + nm
				in_node = true
				continue

			m = _sc_conn_re.search(s)
			if m != null:
				var attrs := _parse_attrs(m.get_string(1))
				if attrs.has("to") and attrs.has("method"):
					connections.append({
						"signal": attrs.get("signal", ""),
						"from": attrs.get("from", "."),
						"to": attrs["to"],
						"method": attrs["method"],
					})
				in_node = false
				continue

			in_node = false  # any other header ends the current node block
			continue

		# Property line inside a node block.
		if in_node:
			var pm := _sc_script_prop_re.search(s)
			if pm != null:
				node_scripts[current_node_path] = pm.get_string(1)

	return {"id_to_path": id_to_path, "node_scripts": node_scripts, "connections": connections}


func _script_has_func(script_res: String, method: String) -> bool:
	var script_text := Scanner.read_text(script_res)
	if script_text == "":
		return true  # unreadable -> skip (MISSING_EXT_RESOURCE reports it separately)
	var re := RegEx.new()
	re.compile("(?m)^\\s*(?:static\\s+)?func\\s+" + Scanner.re_escape(method) + "\\s*\\(")
	return re.search(script_text) != null


# class_name usage collection (port of _collect_class_name_usage)

func _collect_class_name_usage(index: Dictionary) -> Dictionary:
	var decls := {}        # class_name -> declaring script rel (first wins)
	var script_text := {}  # rel -> text
	var cn_re := RegEx.new()
	cn_re.compile("(?m)^\\s*class_name\\s+([A-Za-z_][A-Za-z0-9_]*)")

	for s in index.scripts:
		var t := Scanner.read_text(s)
		if t == "":
			continue
		script_text[s] = t
		var m := cn_re.search(t)
		if m != null and not decls.has(m.get_string(1)):
			decls[m.get_string(1)] = s

	if decls.is_empty():
		return {"script_to_class": {}, "used": {}}

	# Single alternation regex, longest names first, whole-word boundaries.
	var names := decls.keys()
	names.sort_custom(func(a, b): return a.length() > b.length())
	var parts: Array = []
	for nm in names:
		parts.append(Scanner.re_escape(nm))
	var alt := RegEx.new()
	alt.compile("\\b(" + _join_strings(parts, "|") + ")\\b")

	var used := {}
	for rel in script_text:
		for m in alt.search_all(script_text[rel]):
			var nm := m.get_string(1)
			if decls.get(nm, "") != rel:  # appears outside its own declaration file
				used[nm] = true

	for rel in (index.scenes + index.resources):
		var t := Scanner.read_text(rel)
		if t == "":
			continue
		for m in alt.search_all(t):
			used[m.get_string(1)] = true

	var script_to_class := {}
	for cn in decls:
		script_to_class[decls[cn]] = cn
	return {"script_to_class": script_to_class, "used": used}


# Dependency graph + cycle detection (port of graph.py)

func _build_graph(index: Dictionary) -> Dictionary:
	var graph := {}  # source_file -> Array[String] referenced paths (ordered, deduped)
	for ref in index.refs:
		var p: String = ref.path
		if BINARY_REF_EXTS.has(p.get_extension().to_lower()):
			continue
		var src: String = ref.source_file
		if not graph.has(src):
			graph[src] = []
		if not graph[src].has(p):
			graph[src].append(p)
	return graph


func _find_cycles(graph: Dictionary) -> Array:
	# Build adjacency with uniform res:// keys.
	var adj := {}
	for src in graph:
		var src_key := _to_res(src)
		if not adj.has(src_key):
			adj[src_key] = []
		for tgt in graph[src]:
			var tgt_key := _to_res(tgt)
			adj[src_key].append(tgt_key)
			if not adj.has(tgt_key):
				adj[tgt_key] = []

	var found: Array = []
	var seen := {}
	var visited := {}

	var starts := adj.keys()
	starts.sort()
	for start in starts:
		if visited.has(start):
			continue

		var path: Array = []
		var path_set := {}
		var stack: Array = [[start, 0]]

		while stack.size() > 0:
			var top: Array = stack[stack.size() - 1]
			var node: String = top[0]
			var idx: int = top[1]

			if idx == 0:
				if visited.has(node) and not path_set.has(node):
					stack.pop_back()
					continue
				path.append(node)
				path_set[node] = true

			var neighbours: Array = adj.get(node, [])
			var advanced := false
			while idx < neighbours.size():
				var nb: String = neighbours[idx]
				idx += 1
				top[1] = idx
				if path_set.has(nb):
					var cycle_start := path.find(nb)
					var cycle: Array = path.slice(cycle_start)
					var min_pos := 0
					for k in range(cycle.size()):
						if cycle[k] < cycle[min_pos]:
							min_pos = k
					var canonical: Array = cycle.slice(min_pos) + cycle.slice(0, min_pos)
					var key := _join_strings(canonical, "|")
					if not seen.has(key):
						seen[key] = true
						found.append(canonical)
				elif not visited.has(nb):
					stack.append([nb, 0])
					advanced = true
					break

			if not advanced:
				stack.pop_back()
				if path.size() > 0 and path[path.size() - 1] == node:
					path.pop_back()
					path_set.erase(node)
				visited[node] = true

	# Deterministic ordering (cosmetic): sort by the joined cycle path.
	var keyed: Array = []
	for c in found:
		keyed.append([_join_strings(c, "|"), c])
	keyed.sort_custom(func(a, b): return a[0] < b[0])
	var ordered: Array = []
	for k in keyed:
		ordered.append(k[1])
	return ordered


# Resolution helpers

## Resolve a reference path to a res:// path to check, or "" to skip (uid not registered).
func _resolve_ref_res(ref_path: String, source_file: String) -> String:
	if ref_path.begins_with("uid://"):
		var id := ResourceUID.text_to_id(ref_path)
		if ResourceUID.has_id(id):
			return ResourceUID.get_id_path(id)
		return ""  # unresolvable -> skip to avoid false positives
	if ref_path.begins_with("res://"):
		return ref_path
	var source_dir := source_file.get_base_dir()
	return "res://" + Scanner.normalize_posix(source_dir + "/" + ref_path)


## Project-relative canonical path for a reference, or "" if an unresolvable uid.
func _ref_to_canonical_rel(ref_path: String, source_file: String) -> String:
	if ref_path.begins_with("uid://"):
		var id := ResourceUID.text_to_id(ref_path)
		if ResourceUID.has_id(id):
			var rp := ResourceUID.get_id_path(id)
			if rp.begins_with("res://"):
				return rp.substr(6)
		return ""
	if ref_path.begins_with("res://"):
		return ref_path.substr(6)
	var source_dir := source_file.replace("\\", "/").get_base_dir()
	return Scanner.normalize_posix(source_dir + "/" + ref_path)


## True only when a target resolves to a res:// path whose file does not exist.
func _missing_target(path: String) -> bool:
	var res := _resolve_ref_res(path, "")
	if res == "":
		return false  # unresolvable uid:// -> don't flag
	return not FileAccess.file_exists(res)


func _to_res(p: String) -> String:
	var s := p.replace("\\", "/")
	return s if s.begins_with("res://") else "res://" + s


## True when a finding's file lives under addons/ or script_templates/.
func _under_addons(f: String) -> bool:
	if f == "":
		return false
	var n := f.replace("\\", "/")
	for d in ["addons", "script_templates"]:
		if n == d or n.begins_with(d + "/"):
			return true
	return false


# Small utilities

func _issue(code: String, severity: String, message: String, file: String, details: String) -> Dictionary:
	return {
		"code": code,
		"severity": severity,
		"message": message,
		"file": file,
		"details": details,
	}


func _parse_attrs(s: String) -> Dictionary:
	var attrs := {}
	for m in _sc_attr_re.search_all(s):
		attrs[m.get_string(1)] = m.get_string(2)
	return attrs


func _unique_sorted(arr: Array) -> Array:
	var seen := {}
	var out: Array = []
	for x in arr:
		if not seen.has(x):
			seen[x] = true
			out.append(x)
	out.sort()
	return out


func _all_true(d: Dictionary) -> bool:
	for k in d:
		if not d[k]:
			return false
	return true


func _join_strings(arr, sep: String) -> String:
	var s := ""
	for i in arr.size():
		if i > 0:
			s += sep
		s += str(arr[i])
	return s


func _append(dest: Array, src: Array) -> void:
	for item in src:
		dest.append(item)
