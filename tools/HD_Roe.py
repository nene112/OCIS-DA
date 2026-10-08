from __future__ import annotations

import contextlib
import ctypes
import json
import os
from pathlib import Path
from typing import Any


WINDOWS_LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000
WINDOWS_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
PREFERRED_DEPENDENCY_DLLS = (
	"zlib.dll",
	"sqlite3.dll",
	"libsoplexshared.dll",
	"ipopt-3.dll",
	"wdddll.dll",
	"libscip.dll",
 "Gates_Hydraulic.dll",
)


class OcisError(Exception):
	pass


class DllLoadError(OcisError):
	pass


class UnsupportedExportError(OcisError):
	pass


def _find_repo_root(start: Path) -> Path | None:
	for parent in (start, *start.parents):
		if (parent / "src").exists() and (parent / "build").exists():
			return parent
	return None


def _dependency_dirs(dll_path: Path) -> list[Path]:
	module_dir = dll_path.parent.resolve()
	paths = [module_dir]
	repo_root = _find_repo_root(module_dir)
	if repo_root is not None:
		paths.extend(
			[
				repo_root / "build" / "OcisMILPNet_dll" / "Release",
				repo_root / "lib" / "Release",
				repo_root / "lib",
				repo_root / "build" / "OcisMILPNet_bin" / "Release",
				repo_root / "build" / "OcisMILPNet_bin",
				repo_root / "build",
			]
		)

	seen: set[str] = set()
	result: list[Path] = []
	for path in paths:
		if not path.exists():
			continue
		key = str(path.resolve()).lower()
		if key in seen:
			continue
		seen.add(key)
		result.append(path.resolve())
	return result


def _load_dll(dll_path: Path) -> tuple[ctypes.WinDLL, list[object]]:
	handles: list[object] = []
	search_dirs = _dependency_dirs(dll_path)
	if hasattr(os, "add_dll_directory"):
		for directory in search_dirs:
			try:
				handles.append(os.add_dll_directory(str(directory)))
			except OSError:
				pass

	for dll_name in PREFERRED_DEPENDENCY_DLLS:
		for directory in search_dirs:
			candidate = directory / dll_name
			if not candidate.exists():
				continue
			try:
				ctypes.WinDLL(str(candidate))
				break
			except OSError:
				continue

	winmode = WINDOWS_LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | WINDOWS_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
	try:
		return ctypes.WinDLL(str(dll_path), winmode=winmode), handles
	except TypeError:
		return ctypes.WinDLL(str(dll_path)), handles
	except OSError as exc:
		raise DllLoadError(f"加载 DLL 失败: {dll_path}\n{exc}") from exc


def _as_text(value: Any) -> str:
	if isinstance(value, str):
		return value
	if isinstance(value, Path):
		return str(value)
	if isinstance(value, (dict, list)):
		return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
	return str(value)


def _as_bytes(value: Any) -> bytes:
	if isinstance(value, bytes):
		return value
	return _as_text(value).encode("utf-8")


def _as_path_bytes(value: Any) -> bytes:
	if isinstance(value, bytes):
		return value
	text = _as_text(value)
	if os.name == "nt":
		return text.encode("mbcs", errors="replace")
	return os.fsencode(text)


def _decode_char_p(value: bytes | None) -> str:
	if not value:
		return ""
	for encoding in ("utf-8", "gbk", "cp936", "mbcs"):
		try:
			return value.decode(encoding)
		except UnicodeDecodeError:
			continue
	return value.decode("utf-8", errors="replace")


@contextlib.contextmanager
def pushd(path: str | Path):
	previous = Path.cwd()
	os.chdir(Path(path))
	try:
		yield
	finally:
		os.chdir(previous)


class OcisMILPNet:
	def __init__(self, dll_path: str | Path | None = None) -> None:
		self.dll_path = Path(dll_path) if dll_path else Path(__file__).with_name("OcisMILPNet.dll")
		self.dll_path = self.dll_path.resolve()
		if not self.dll_path.exists():
			raise DllLoadError(f"找不到 DLL: {self.dll_path}")
		self._dll, self._dll_dir_handles = _load_dll(self.dll_path)
		self._bind_exports()
		self.obj = self._create()
		if not self.obj:
			raise OcisError("createOcisMILPNetLab 返回空指针")

	def _bind(self, name: str, argtypes: list[Any], restype: Any) -> Any:
		func = getattr(self._dll, name)
		func.argtypes = argtypes
		func.restype = restype
		return func

	def _bind_optional(self, name: str, argtypes: list[Any], restype: Any) -> Any:
		func = getattr(self._dll, name, None)
		if func is None:
			return None
		func.argtypes = argtypes
		func.restype = restype
		return func

	def _bind_exports(self) -> None:
		self._create = self._bind("createOcisMILPNetLab", [], ctypes.c_void_p)
		self._set_time_param_MILP = self._bind("set_time_param_MILP", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_time_param = self._bind("set_time_param", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._get_time_param = self._bind("get_time_param", [ctypes.c_void_p], ctypes.c_char_p)
		self._get_input_json = self._bind("get_input_json", [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_char_p)
		self._step_k = self._bind("step_k", [ctypes.c_void_p], None)
  
		self._set_step_t = self._bind("set_step_t", [ctypes.c_void_p, ctypes.c_double], ctypes.c_double)
		self._set_delta_t = self._bind("set_delta_t", [ctypes.c_void_p, ctypes.c_double], ctypes.c_int)
  
		self._read_data_sim = self._bind("read_data_sim", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._read_data_sim_Roe = self._bind("read_data_sim_Roe", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._read_data_sim_Roe_c_str = self._bind("read_data_sim_Roe_c_str", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._read_data_sim_Roe_c_str_pools = self._bind_optional("read_data_sim_Roe_c_str_pools", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._read_data_sim_char = self._bind("read_data_sim_char", [ctypes.c_void_p, ctypes.c_char_p], None)
  
		self._Split_mesh = self._bind("Split_mesh", [ctypes.c_void_p], None)
		self._Auto_correct_endpoint = self._bind("Auto_correct_endpoint", [ctypes.c_void_p], None)
		self._set_CrossSection = self._bind_optional("set_CrossSection", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int], None)
		self._Auto_correct_gates_calculationType = self._bind("Auto_correct_gates_calculationType", [ctypes.c_void_p], None)
  
		self._stepSolver_sim = self._bind("stepSolver_sim", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)], None)
		self._stepSolver_sim_Roe = self._bind("stepSolver_sim_Roe", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.c_char_p], None)
		self._update_BC_sim_only = self._bind("update_BC_sim_only", [ctypes.c_void_p, ctypes.c_int], None)
		self._stepSolver_sim_Roe_only = self._bind("stepSolver_sim_Roe_only", [ctypes.c_void_p, ctypes.c_int], None)
		self._stepSolver_sim_Roe_only_pool = self._bind_optional("stepSolver_sim_Roe_only_pool", [ctypes.c_void_p, ctypes.c_int, ctypes.c_int], None)
		self._check_nan_sim = self._bind_optional("check_nan_sim", [ctypes.c_void_p], ctypes.c_int)
		self._stepSolver_sim_noUpdate = self._bind("stepSolver_sim_noUpdate", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)], None)
		

		self._get_Total_T_step_count =self._bind("get_Total_T_step_count", [ctypes.c_void_p], ctypes.c_int)
		self._get_stepdata_sim = self._bind("get_stepdata_sim", [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_char_p)
		self._get_pool_info_sim = self._bind("get_pool_info_sim", [ctypes.c_void_p], ctypes.c_char_p)
		self._get_gate_info_sim = self._bind("get_gate_info_sim", [ctypes.c_void_p], ctypes.c_char_p)
      	
       # hydraudynamic - set
		self._set_GatesBC_sim = self._bind("set_GatesBC_sim", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_GatesFlow_bynIndex_sim = self._bind("set_GatesFlow_bynIndex_sim", [ctypes.c_void_p, ctypes.c_int, ctypes.c_double], None)
		self._set_GatesFlow_byID_sim = self._bind("set_GatesFlow_byID_sim", [ctypes.c_void_p, ctypes.c_int, ctypes.c_double], None)
		self._set_GatesFlow_mu_byID_sim = self._bind_optional("set_GatesFlow_mu_byID_sim", [ctypes.c_void_p, ctypes.c_int, ctypes.c_double], None)
		self._set_GatesFlow_mu_byID_sim_multi = self._bind_optional("set_GatesFlow_mu_byID_sim_multi", [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p], None)
		self._set_GatesFlow_e_bynIndex_sim = self._bind("set_GatesFlow_e_bynIndex_sim", [ctypes.c_void_p, ctypes.c_int, ctypes.c_double], None)
		self._set_GatesFlow_e_byID_sim = self._bind("set_GatesFlow_e_byID_sim", [ctypes.c_void_p, ctypes.c_int, ctypes.c_double], None)
		self._set_outputfile_Label = self._bind("set_outputfile_Label", [ctypes.c_void_p, ctypes.c_int], None)
  
  
		self._set_action_sim = self._bind("set_action_sim", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_action_sim_json = self._bind("set_action_sim_json", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._clear_action_sim = self._bind("clear_action_sim", [ctypes.c_void_p], None)
		self._set_pool_param_sim = self._bind("set_pool_param_sim", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_Algorithm_sim = self._bind("set_Algorithm_sim", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_calculation_range_by_pool = self._bind_optional("set_calculation_range_by_pool", [ctypes.c_void_p, ctypes.c_int], None)
		self._set_all_inih = self._bind("set_all_inih", [ctypes.c_void_p, ctypes.c_double], None)
		self._set_all_zb = self._bind_optional("set_all_zb", [ctypes.c_void_p, ctypes.c_double], None)
		self._set_inih_1_byGates = self._bind_optional("set_inih_1_byGates", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)], None)
		self._set_all_manning = self._bind("set_all_manning", [ctypes.c_void_p, ctypes.c_double], None)
		self._set_all_gates_byType = self._bind("set_all_gates_byType", [ctypes.c_void_p, ctypes.c_double, ctypes.c_int], None)
		self._save_states = self._bind("save_states", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_states = self._bind("set_states", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._reset_states = self._bind("reset_states", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._reset_state_sim = self._bind("reset_state_sim", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._write_boundary_condition_flow = self._bind("write_boundary_condition_flow", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._write_result_sim = self._bind("write_result_sim", [ctypes.c_void_p], None)
		self._write_result_sim_suffix = self._bind("write_result_sim_suffix", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p], None)
		self._read_data_MILP = self._bind("read_data_MILP", [ctypes.c_void_p], None)
		self._read_data_MILP_path = self._bind("read_data_MILP_path", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._read_data_MILP_c_str = self._bind("read_data_MILP_c_str", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._Auto_correct_gates_maxFlow = self._bind("Auto_correct_gates_maxFlow", [ctypes.c_void_p], None)
		self._stepSolver_MILP = self._bind("stepSolver_MILP", [ctypes.c_void_p, ctypes.c_int], None)
		self._get_step_action_MILP = self._bind("get_step_action_MILP", [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_char_p)
		self._get_step_action_MILP_write = self._bind("get_step_action_MILP_write", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_char_p)
		self._set_action_MILP = self._bind("set_action_MILP", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_action_MILP_json = self._bind("set_action_MILP_json", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_h1_MILP_json = self._bind("set_h1_MILP_json", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._set_IC_MILP_json = self._bind("set_IC_MILP_json", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p], None)
		self._MILP_write_output = self._bind("MILP_write_output", [ctypes.c_void_p, ctypes.c_char_p], None)
		self._GateFlow_hydraulic = self._bind(
			"GateFlow_hydraulic",
			[
				ctypes.POINTER(ctypes.c_double),
				ctypes.POINTER(ctypes.c_double),
				ctypes.POINTER(ctypes.c_double),
				ctypes.POINTER(ctypes.c_double),
				ctypes.POINTER(ctypes.c_double),
				ctypes.POINTER(ctypes.c_double),
			],
			ctypes.c_double,
		)

	def set_time_param_MILP(self, time_obj: Any) -> None:
		self._set_time_param_MILP(self.obj, _as_bytes(time_obj))

	def set_time_param(self, time_obj: Any) -> None:
		self._set_time_param(self.obj, _as_bytes(time_obj))

	def get_time_param(self) -> str:
		return _decode_char_p(self._get_time_param(self.obj))

	def get_input_json(self, dataConfig_path_c_str: str | Path) -> str:
		return _decode_char_p(self._get_input_json(self.obj, _as_path_bytes(dataConfig_path_c_str)))

	def step_k(self) -> None:
		self._step_k(self.obj)

	def char2json(self, intput_c_str: Any) -> None:
		raise UnsupportedExportError("char2json 返回 C++ json 对象，ctypes 不能直接安全调用")

	def set_step_t(self, value: float) -> float:
		self._set_step_t(self.obj,value)
  
	def set_delta_t(self, value: int) -> int:
		return self._set_delta_t(self.obj, value)


	def read_data_sim(self, path: str | Path) -> None:
		self._read_data_sim(self.obj, _as_path_bytes(path))

	def read_data_sim_Roe(self, path: str | Path) -> None:
		self._read_data_sim_Roe(self.obj, _as_path_bytes(path))

	def read_data_sim_Roe_c_str(self, dataConfig_c_str: Any) -> None:
		self._read_data_sim_Roe_c_str(self.obj, _as_bytes(dataConfig_c_str))

	def read_data_sim_Roe_c_str_pools(self, dataConfig_c_str: Any) -> None:
		if self._read_data_sim_Roe_c_str_pools is None:
			raise UnsupportedExportError("当前 DLL 未导出 read_data_sim_Roe_c_str_pools")
		self._read_data_sim_Roe_c_str_pools(self.obj, _as_bytes(dataConfig_c_str))

	def read_data_sim_char(self, dataconfigjson_c_str: Any) -> None:
		self._read_data_sim_char(self.obj, _as_bytes(dataconfigjson_c_str))

	def Split_mesh(self) -> None:
		self._Split_mesh(self.obj)

	def set_CrossSection(self, path: str | Path, cs_start: int = 0, cs_end: int | None = None) -> None:
		"""导入指定JSON/GeoJSON或目录；需先初始化网格。DLL仅更新床面高程。

		单文件暂存于独立目录，防止DLL扫描同目录下其他GeoJSON。
		目录方式会在该目录下写出ReWriteMesh网格。
		"""
		if self._set_CrossSection is None:
			raise UnsupportedExportError("当前 DLL 未导出 set_CrossSection")
		import math
		import shutil
		import tempfile
		location = Path(path).resolve()
		if not location.exists():
			raise FileNotFoundError(location)
		files = [location] if location.is_file() else sorted(location.glob("*geojson*"))
		if not files:
			raise ValueError("目录中没有GeoJSON文件")
		counts = []
		for file in files:
			root = json.loads(file.read_text(encoding="utf-8-sig"))
			features = root.get("features")
			if not isinstance(features, list):
				raise ValueError(f"{file.name} 缺少features数组")
			for feature in features:
				geometry = feature.get("geometry") or {}
				coords = geometry.get("coordinates")
				if not isinstance(coords, list) or len(coords) < 3 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in coords[:3]):
					raise ValueError(f"{file.name}: 断面缺少有效三维位置坐标；不能将null视为已导入")
			counts.append(len(features))
		if cs_end is None:
			cs_end = min(counts) - 1
		if not isinstance(cs_start, int) or not isinstance(cs_end, int) or cs_start < 0 or cs_end < cs_start or any(cs_end >= count for count in counts):
			raise ValueError("断面索引范围无效（从0开始，包含起止索引）")
		if location.is_file():
			with tempfile.TemporaryDirectory(prefix="ocis_cross_section_") as directory:
				shutil.copyfile(location, Path(directory) / "CrossSection.geojson")
				self._set_CrossSection(self.obj, _as_path_bytes(directory + "/"), cs_start, cs_end)
		else:
			self._set_CrossSection(self.obj, _as_path_bytes(str(location) + "/"), cs_start, cs_end)

	def Auto_correct_endpoint(self) -> None:
		self._Auto_correct_endpoint(self.obj)

	def Auto_correct_gates_calculationType(self) -> None:
		self._Auto_correct_gates_calculationType(self.obj)


	def stepSolver_sim(self, k: int) -> int:
		step = ctypes.c_int(k)
		self._stepSolver_sim(self.obj, ctypes.byref(step))
		return step.value

	def stepSolver_sim_Roe(self, k: int, taskType: str = "") -> int:
		step = ctypes.c_int(k)
		self._stepSolver_sim_Roe(self.obj, ctypes.byref(step), _as_bytes(taskType))
		return step.value

	def update_BC_sim_only(self, k: int) -> None:
		self._update_BC_sim_only(self.obj, int(k))

	def stepSolver_sim_Roe_only(self, k: int) -> None:
		self._stepSolver_sim_Roe_only(self.obj, int(k))

	def stepSolver_sim_Roe_only_pool(self, k: int, pool_id: int) -> None:
		if self._stepSolver_sim_Roe_only_pool is None:
			raise UnsupportedExportError("当前 DLL 未导出 stepSolver_sim_Roe_only_pool")
		self._stepSolver_sim_Roe_only_pool(self.obj, int(k), int(pool_id))

	def check_nan_sim(self) -> bool:
		if self._check_nan_sim is None:
			return False
		return bool(int(self._check_nan_sim(self.obj)))

	def stepSolver_sim_noUpdate(self, k: int) -> int:
		step = ctypes.c_int(k)
		self._stepSolver_sim_noUpdate(self.obj, ctypes.byref(step))
		return step.value

	def get_stepdata_sim(self, datatype: str) -> str:
		return _decode_char_p(self._get_stepdata_sim(self.obj, _as_bytes(datatype)))

	def get_Total_T_step_count(self) :
		return self._get_Total_T_step_count(self.obj)

	def get_pool_info_sim(self) -> str:
		return _decode_char_p(self._get_pool_info_sim(self.obj))

	def get_gate_info_sim(self) -> str:
		return _decode_char_p(self._get_gate_info_sim(self.obj))

	def set_GatesBC_sim(self, GatesBC_obj: Any) -> None:
		self._set_GatesBC_sim(self.obj, _as_bytes(GatesBC_obj))

	def set_GatesFlow_bynIndex_sim(self, nIndex: int, flow: float) -> None:
		self._set_GatesFlow_bynIndex_sim(self.obj, int(nIndex), float(flow))

	def set_GatesFlow_byID_sim(self, gate_id: str | int, flow: float) -> None:
		self._set_GatesFlow_byID_sim(self.obj, int(gate_id), float(flow))

	def set_GatesFlow_mu_byID_sim(self, gate_id: str | int, mu: float) -> None:
		if self._set_GatesFlow_mu_byID_sim is None:
			raise UnsupportedExportError("当前 DLL 未导出 set_GatesFlow_mu_byID_sim")
		self._set_GatesFlow_mu_byID_sim(self.obj, int(gate_id), float(mu))

	def set_GatesFlow_mu_byID_sim_multi(self, gate_id: str | int, mu_obj: Any) -> None:
		if self._set_GatesFlow_mu_byID_sim_multi is None:
			raise UnsupportedExportError("当前 DLL 未导出 set_GatesFlow_mu_byID_sim_multi")
		self._set_GatesFlow_mu_byID_sim_multi(self.obj, int(gate_id), _as_bytes(mu_obj))

 
	def set_GatesFlow_e_bynIndex_sim(self, nIndex: int, e: float) -> None:
		self._set_GatesFlow_e_bynIndex_sim(self.obj, int(nIndex), float(e))

	def set_GatesFlow_e_byID_sim(self, gate_id: str | int, e: float) -> None:
		self._set_GatesFlow_e_byID_sim(self.obj, int(gate_id), float(e))

	def set_outputfile_Label(self, label: int) -> None:
		self._set_outputfile_Label(self.obj, int(label))
  
  

	def set_action_sim(self, action_j_obj: Any) -> None:
		self._set_action_sim(self.obj, _as_bytes(action_j_obj))

	def set_action_sim_json(self, action_j_json: Any) -> None:
		self._set_action_sim_json(self.obj, _as_bytes(action_j_json))

	def clear_action_sim(self) -> None:
		self._clear_action_sim(self.obj)

	def set_pool_param_sim(self, param_obj: Any) -> None:
		self._set_pool_param_sim(self.obj, _as_bytes(param_obj))

	def set_calculation_range_by_pool(self, pool_id: int) -> None:
		if self._set_calculation_range_by_pool is None:
			raise UnsupportedExportError("当前 DLL 未导出 set_calculation_range_by_pool")
		self._set_calculation_range_by_pool(self.obj, int(pool_id))

	def set_Algorithm_sim(self, param_obj: Any) -> None:
		self._set_Algorithm_sim(self.obj, _as_bytes(param_obj))

	def set_all_inih(self, value: float) -> None:
		self._set_all_inih(self.obj, float(value))

	def set_all_zb(self, value: float) -> None:
		if self._set_all_zb is None:
			raise UnsupportedExportError("当前 DLL 未导出 set_all_zb")
		self._set_all_zb(self.obj, float(value))

	def set_inih_1_byGates(self, gates_inih_obj: Any) -> None:
		def _fallback_set_all() -> None:
			vals = []
			if isinstance(gates_inih_obj, dict) and gates_inih_obj:
				for v in gates_inih_obj.values():
					try:
						vals.append(float(v))
					except Exception:
						pass
			elif isinstance(gates_inih_obj, (list, tuple)) and gates_inih_obj:
				for v in gates_inih_obj:
					try:
						vals.append(float(v))
					except Exception:
						pass
			if vals:
				self._set_all_inih(self.obj, float(sum(vals) / len(vals)))

		if self._set_inih_1_byGates is None:
			# 兼容旧 DLL：没有逐闸门接口时退化为全局初始水深。
			_fallback_set_all()
			return

		try:
			arr_vals: list[float] = []
			if isinstance(gates_inih_obj, dict):
				idx_vals: dict[int, float] = {}
				for k, v in gates_inih_obj.items():
					try:
						idx = int(k)
						idx_vals[idx] = float(v)
					except Exception:
						continue
				if not idx_vals:
					_fallback_set_all()
					return
				max_idx = max(idx_vals.keys())
				default_v = float(sum(idx_vals.values()) / len(idx_vals))
				arr_vals = [default_v] * (max_idx + 1)
				for idx, val in idx_vals.items():
					if idx >= 0:
						arr_vals[idx] = val
			elif isinstance(gates_inih_obj, (list, tuple)):
				arr_vals = [float(v) for v in gates_inih_obj]
			else:
				_fallback_set_all()
				return

			if not arr_vals:
				_fallback_set_all()
				return

			arr_type = ctypes.c_double * len(arr_vals)
			arr = arr_type(*arr_vals)
			self._set_inih_1_byGates(self.obj, arr)
		except Exception:
			# 部分 DLL 虽导出函数，但参数协议不兼容；避免崩溃并降级。
			_fallback_set_all()

	def set_all_manning(self, value: float) -> None:
		self._set_all_manning(self.obj, float(value))

	def set_all_gates_byType(self, value: float, type: int) -> None:
		self._set_all_gates_byType(self.obj, float(value), int(type))

	def save_states(self, filename: str = "") -> None:
		"""保存内存试算快照：水力变量、时钟、闸门和结果缓存；filename 为兼容参数。"""
		self._save_states(self.obj, _as_path_bytes(filename))

	def set_states(self, filename: str = "") -> None:
		"""恢复最近的试算快照，供同一交互周期重复尝试控制量。"""
		self._set_states(self.obj, _as_path_bytes(filename))

	def reset_states(self, filename: str = "") -> None:
		self._reset_states(self.obj, _as_path_bytes(filename))

	def reset_state_sim(self, timestamp: str = "") -> None:
		self._reset_state_sim(self.obj, _as_bytes(timestamp))

	def write_boundary_condition_flow(self, filename: str | Path) -> None:
		self._write_boundary_condition_flow(self.obj, _as_path_bytes(filename))

	def write_result_sim(self) -> None:
		self._write_result_sim(self.obj)

	def write_result_sim_suffix(self, dirpath: str | Path, suffix: str) -> None:
		self._write_result_sim_suffix(self.obj, _as_path_bytes(dirpath), _as_bytes(suffix))

	def read_data_MILP(self) -> None:
		self._read_data_MILP(self.obj)

	def read_data_MILP_path(self, dataconfig_path: str | Path) -> None:
		self._read_data_MILP_path(self.obj, _as_path_bytes(dataconfig_path))

	def read_data_MILP_c_str(self, dataconfigjson_c_str: Any) -> None:
		self._read_data_MILP_c_str(self.obj, _as_bytes(dataconfigjson_c_str))

	def Auto_correct_gates_maxFlow(self) -> None:
		self._Auto_correct_gates_maxFlow(self.obj)

	def stepSolver_MILP(self, episode: int) -> None:
		self._stepSolver_MILP(self.obj, int(episode))

	def get_step_action_MILP(self, datatype: str) -> str:
		return _decode_char_p(self._get_step_action_MILP(self.obj, _as_bytes(datatype)))

	def get_step_action_MILP_write(self, dirpath: str | Path, filename: str) -> str:
		return _decode_char_p(self._get_step_action_MILP_write(self.obj, _as_path_bytes(dirpath), _as_path_bytes(filename)))

	def set_action_MILP(self, action_j_obj: Any) -> None:
		self._set_action_MILP(self.obj, _as_bytes(action_j_obj))

	def set_action_MILP_json(self, action_j_json: Any) -> None:
		self._set_action_MILP_json(self.obj, _as_bytes(action_j_json))

	def set_h1_MILP_json(self, h1_j_json: Any) -> None:
		self._set_h1_MILP_json(self.obj, _as_bytes(h1_j_json))

	def set_IC_MILP_json(self, h1_j_json: Any, datatype: str) -> None:
		self._set_IC_MILP_json(self.obj, _as_bytes(h1_j_json), _as_bytes(datatype))

	def MILP_write_output(self, suffix: str = "") -> None:
		self._MILP_write_output(self.obj, _as_bytes(suffix))

	def read_data_wd(self, path: str | Path) -> None:
		self._read_data_wd(self.obj, _as_path_bytes(path))

	def stepSolver_wd_irri(self, irrigations_c_str: Any) -> None:
		self._stepSolver_wd_irri(self.obj, _as_bytes(irrigations_c_str))

	def stepSolver_wd_demand(self, demand_c_str: Any) -> str:
		return _decode_char_p(self._stepSolver_wd_demand(self.obj, _as_bytes(demand_c_str)))

	def GateFlow_hydraulic(self, eg: float, mu: float, bg: float, Hu: float, Hd: float, Q_set: float) -> float:
		eg_v = ctypes.c_double(float(eg))
		mu_v = ctypes.c_double(float(mu))
		bg_v = ctypes.c_double(float(bg))
		Hu_v = ctypes.c_double(float(Hu))
		Hd_v = ctypes.c_double(float(Hd))
		Q_set_v = ctypes.c_double(float(Q_set))
		return float(
			self._GateFlow_hydraulic(
				ctypes.byref(eg_v),
				ctypes.byref(mu_v),
				ctypes.byref(bg_v),
				ctypes.byref(Hu_v),
				ctypes.byref(Hd_v),
				ctypes.byref(Q_set_v),
			)
		)

	def GateFlow(self, eg: float, mu: float, bg: float, Hu: float, Hd: float, Q_set: float) -> float:
		return self.GateFlow_hydraulic(eg=eg, mu=mu, bg=bg, Hu=Hu, Hd=Hd, Q_set=Q_set)

def create_client(dll_path: str | Path | None = None) -> OcisMILPNet:
	return OcisMILPNet(dll_path)
