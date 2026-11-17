-- pulseSSS for OBS: starts the heart-rate app with OBS and adds the overlay to a scene.
-- Install: OBS > Tools > Scripts > + > pick this file.

obs = obslua
local ffi = require("ffi")

ffi.cdef[[
int MultiByteToWideChar(unsigned int cp, unsigned long flags, const char* s, int n, wchar_t* out, int outn);
void* ShellExecuteW(void* hwnd, const wchar_t* op, const wchar_t* file, const wchar_t* params, const wchar_t* dir, int show);
unsigned long GetCurrentProcessId(void);
]]
local shell32 = ffi.load("shell32")

local cfg = { python = "", folder = "", port = 8765, autostart = true, stop_with_obs = false, design = "", source = "Heart rate" }

local function wide(s)
  if s == nil then return nil end
  local n = ffi.C.MultiByteToWideChar(65001, 0, s, -1, nil, 0)
  local buf = ffi.new("wchar_t[?]", n)
  ffi.C.MultiByteToWideChar(65001, 0, s, -1, buf, n)
  return buf
end

local function shell(file, params, dir, show)
  local r = shell32.ShellExecuteW(nil, wide("open"), wide(file), wide(params), wide(dir), show)
  return tonumber(ffi.cast("intptr_t", r)) > 32
end

local function exists(path)
  local f = io.open(path, "rb")
  if f then f:close() return true end
  return false
end

local function log(msg) obs.script_log(obs.LOG_INFO, msg) end

local function default_folder()
  local dir = script_path():gsub("[/\\]+$", "")
  return (dir:gsub("[/\\][^/\\]+$", ""))
end

local function default_python()
  local la = os.getenv("LOCALAPPDATA") or ""
  local candidates = { la .. "\\Python\\pythoncore-3.14-64\\pythonw.exe" }
  for v = 14, 10, -1 do
    table.insert(candidates, la .. "\\Python\\pythoncore-3." .. v .. "-64\\pythonw.exe")
    table.insert(candidates, la .. "\\Programs\\Python\\Python3" .. v .. "\\pythonw.exe")
  end
  for _, p in ipairs(candidates) do
    if exists(p) then return p end
  end
  return "pythonw.exe"
end

local function base_url() return "http://127.0.0.1:" .. cfg.port end

local function overlay_url()
  local url = base_url() .. "/overlay"
  if cfg.design ~= "" then
    url = url .. "?design=" .. cfg.design:gsub("[^%w%-_.~]", function(c) return string.format("%%%02X", c:byte()) end)
  end
  return url
end

-- Browser sources that loaded before the app was up show nothing; reload them.
local function refresh_overlays()
  local prefix = base_url()
  local sources = obs.obs_enum_sources()
  if sources then
    for _, src in ipairs(sources) do
      if obs.obs_source_get_unversioned_id(src) == "browser_source" then
        local s = obs.obs_source_get_settings(src)
        local url = obs.obs_data_get_string(s, "url")
        obs.obs_data_release(s)
        if url:sub(1, #prefix) == prefix then
          local props = obs.obs_source_properties(src)
          local btn = obs.obs_properties_get(props, "refreshnocache")
          if btn then obs.obs_property_button_clicked(btn, src) end
          obs.obs_properties_destroy(props)
        end
      end
    end
    obs.source_list_release(sources)
  end
end

local refresh_count = 0
local function refresh_tick()
  refresh_overlays()
  refresh_count = refresh_count + 1
  if refresh_count >= 2 then obs.timer_remove(refresh_tick) end
end

-- Launches the pulseSSS tray app. If it's already running (e.g. from the desktop shortcut or
-- Windows sign-in) the new copy exits straight away and the existing connection is kept.
local function start_app()
  local launcher = cfg.folder .. "\\pulseSSS.pyw"
  if not exists(launcher) then
    log("pulseSSS: pulseSSS.pyw not found in " .. cfg.folder .. " - set the pulseSSS folder in the script settings.")
    return false
  end
  local params = string.format('"%s" --quiet --port %d', launcher, cfg.port)
  if cfg.stop_with_obs then
    params = params .. string.format(" --watch-pid %d", tonumber(ffi.C.GetCurrentProcessId()))
  end
  if not shell(cfg.python, params, cfg.folder, 0) then
    log("pulseSSS: couldn't start " .. cfg.python .. " - check the Python path in the script settings.")
    return false
  end
  log("pulseSSS: tray app launched (" .. base_url() .. ")")
  -- Reload overlays after the server has had time to start (and once more for slow Bluetooth stacks).
  obs.timer_remove(refresh_tick)
  refresh_count = 0
  obs.timer_add(refresh_tick, 5000)
  return true
end

local function add_to_scene()
  local scene_src = obs.obs_frontend_get_current_scene()
  if not scene_src then return false end
  local scene = obs.obs_scene_from_source(scene_src)
  local src = obs.obs_get_source_by_name(cfg.source)
  if src == nil then
    local s = obs.obs_data_create()
    obs.obs_data_set_string(s, "url", overlay_url())
    obs.obs_data_set_int(s, "width", 420)
    obs.obs_data_set_int(s, "height", 200)
    obs.obs_data_set_bool(s, "shutdown", false)
    src = obs.obs_source_create("browser_source", cfg.source, s, nil)
    obs.obs_data_release(s)
  else
    local s = obs.obs_source_get_settings(src)
    obs.obs_data_set_string(s, "url", overlay_url())
    obs.obs_source_update(src, s)
    obs.obs_data_release(s)
  end
  if obs.obs_scene_find_source(scene, cfg.source) == nil then
    obs.obs_scene_add(scene, src)
  end
  obs.obs_source_release(src)
  obs.obs_source_release(scene_src)
  log("pulseSSS: overlay in scene (" .. overlay_url() .. ")")
  return false
end

local function read_settings(settings)
  cfg.python = obs.obs_data_get_string(settings, "python")
  cfg.folder = obs.obs_data_get_string(settings, "folder")
  cfg.port = obs.obs_data_get_int(settings, "port")
  cfg.autostart = obs.obs_data_get_bool(settings, "autostart")
  cfg.stop_with_obs = obs.obs_data_get_bool(settings, "stop_with_obs")
  cfg.design = obs.obs_data_get_string(settings, "design")
  cfg.source = obs.obs_data_get_string(settings, "source")
  if cfg.python == "" then cfg.python = default_python() end
  if cfg.folder == "" then cfg.folder = default_folder() end
  cfg.folder = cfg.folder:gsub("/", "\\"):gsub("\\+$", "")
  if cfg.port == 0 then cfg.port = 8765 end
  if cfg.source == "" then cfg.source = "Heart rate" end
end

function script_description()
  return [[<h3>pulseSSS heart rate</h3>
<p>Makes sure the pulseSSS tray app is running when OBS opens. It stays connected to your
Fitbit Air after OBS closes, so the next stream starts instantly. Quit it from its tray icon.
Turn on <b>Share heart rate</b> in Google Health first.</p>
<p>Change the overlay's look in the designer: <a href="http://127.0.0.1:8765/design">127.0.0.1:8765/design</a>
(also works as a dock: Docks &gt; Custom Browser Docks).</p>]]
end

function script_properties()
  local p = obs.obs_properties_create()
  obs.obs_properties_add_bool(p, "autostart", "Start pulseSSS with OBS (if not already running)")
  obs.obs_properties_add_bool(p, "stop_with_obs", "Quit pulseSSS when OBS closes (reconnects every time)")
  obs.obs_properties_add_button(p, "start", "Start pulseSSS now", function() start_app() return false end)
  obs.obs_properties_add_button(p, "add", "Add overlay to current scene", function() add_to_scene() return false end)
  obs.obs_properties_add_button(p, "refresh", "Reload overlays", function() refresh_overlays() return false end)
  obs.obs_properties_add_button(p, "designer", "Open designer", function() shell(base_url() .. "/design", nil, nil, 1) return false end)
  obs.obs_properties_add_text(p, "design", "Design for new overlay (empty = default)", obs.OBS_TEXT_DEFAULT)
  obs.obs_properties_add_text(p, "source", "Source name", obs.OBS_TEXT_DEFAULT)
  obs.obs_properties_add_path(p, "python", "pythonw.exe", obs.OBS_PATH_FILE, "Programs (*.exe)", nil)
  obs.obs_properties_add_path(p, "folder", "pulseSSS folder", obs.OBS_PATH_DIRECTORY, nil, nil)
  obs.obs_properties_add_int(p, "port", "Port", 1024, 65535, 1)
  return p
end

function script_defaults(settings)
  obs.obs_data_set_default_bool(settings, "autostart", true)
  obs.obs_data_set_default_bool(settings, "stop_with_obs", false)
  obs.obs_data_set_default_string(settings, "python", default_python())
  obs.obs_data_set_default_string(settings, "folder", default_folder())
  obs.obs_data_set_default_int(settings, "port", 8765)
  obs.obs_data_set_default_string(settings, "source", "Heart rate")
end

function script_update(settings)
  read_settings(settings)
end

function script_load(settings)
  read_settings(settings)
  if cfg.autostart then start_app() end
end

function script_unload()
  obs.timer_remove(refresh_tick)
end
