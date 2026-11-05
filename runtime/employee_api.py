"""DDZM 员工只读接入；职级可按明确配置的顺序比较，不使用游戏额度推导。"""
import json
import os
import threading
import time
from urllib.parse import quote, urlsplit

import requests


class EmployeeUnavailable(ValueError):
    """仅包含可以向群友展示的固定提示，不携带请求或密钥。"""


class Client:
    _lock = threading.Lock()
    _calls = []

    def __init__(self):
        self.base = os.environ.get("DZMM_INTEGRATION_BASE_URL", "").strip().rstrip("/")
        self.key = os.environ.get("DZMM_INTEGRATION_API_KEY", "").strip()
        # 不把建议中的 /profile 当作已经存在的正式接口。
        self.profile_template = os.environ.get("DZMM_EMPLOYEE_PROFILE_PATH", "").strip()
        self.profile_mode = os.environ.get("DZMM_EMPLOYEE_PROFILE_MODE", "profile").strip()
        self.rank_order = os.environ.get("DZMM_EMPLOYEE_RANK_ORDER", "").strip()

    def _request(self, method, path, body=None):
        if not self.key or not self.base:
            raise EmployeeUnavailable("员工接口尚未配置，这次不能开始，也不会扣次数。")
        parsed = urlsplit(self.base)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path):
            raise EmployeeUnavailable("员工接口配置不完整，请联系管理员。")
        with self._lock:
            now = time.monotonic()
            self._calls[:] = [stamp for stamp in self._calls if now - stamp < 60]
            # 留出余量给其他集成用途；进程内限速，服务端 429 仍为最终依据。
            if len(self._calls) >= 45:
                raise EmployeeUnavailable("员工资料查询有点忙，稍后再试，不会扣次数。")
            self._calls.append(now)
        try:
            response = requests.request(method, self.base + path, json=body,
                                        headers={"X-Api-Key": self.key}, timeout=(3, 7),
                                        allow_redirects=False)
            if response.status_code == 404:
                raise EmployeeUnavailable("没有找到已入职员工，请核对 @ 或工号。")
            if response.status_code != 200:
                raise EmployeeUnavailable("员工资料暂时查询失败，请稍后再试，不会扣次数。")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("shape")
            return data
        except (requests.RequestException, ValueError) as error:
            if isinstance(error, EmployeeUnavailable):
                raise
            raise EmployeeUnavailable("员工资料暂时查询失败，请稍后再试，不会扣次数。") from None

    def match(self, target):
        if target.startswith("#") and target[1:].isdigit():
            field, value = "employee_number", target
        else:
            field, value = "name", target.lstrip("@")
        data = self._request("POST", "/users/match", {field: value})
        rows = data.get("matches")
        if data.get("status") == "ambiguous":
            raise EmployeeUnavailable("这个名字对应多位员工，请改用真实 @ 或工号。")
        if data.get("status") != "matched" or not isinstance(rows, list) or len(rows) != 1:
            raise EmployeeUnavailable("没有找到唯一的已入职员工，请改用真实 @ 或工号。")
        uid = rows[0].get("platform_id") if isinstance(rows[0], dict) else None
        if not isinstance(uid, str) or not uid.strip():
            raise EmployeeUnavailable("员工身份资料不完整，请联系管理员。")
        return uid

    def profile(self, uid):
        template = self.profile_template
        if template.count("{platform_id}") != 1 or "{" in template.replace("{platform_id}", ""):
            raise EmployeeUnavailable("职级资料接口尚未配置，这次不会扣次数。")
        if self.profile_mode not in ("profile", "game_quota"):
            raise EmployeeUnavailable("员工资料接口类型配置不完整，请联系管理员。")
        levels = configured_rank_levels(self.rank_order) if self.profile_mode == "game_quota" else None
        data = self._request("GET", template.replace("{platform_id}", quote(uid, safe="")))
        if levels is not None:
            rank = data.get("rank_name")
            rank = rank.strip() if isinstance(rank, str) else ""
            if rank not in levels:
                raise EmployeeUnavailable("暂时无法判断这个职级的高低，请联系管理员确认职级顺序，不会扣次数。")
            # game-quota 仅提供职级名称；高低来自管理员确认的顺序，与额度字段无关。
            data = {**data, "rank_name": rank, "rank_level": levels[rank]}
        return normalize_profile(data, uid)


def configured_rank_levels(raw):
    try:
        ranks = json.loads(raw)
    except (TypeError, ValueError):
        ranks = None
    if (not isinstance(ranks, list) or not ranks
            or any(not isinstance(name, str) or not name.strip() for name in ranks)):
        raise EmployeeUnavailable("职级顺序尚未正确配置，这次不会扣次数。")
    names = [name.strip() for name in ranks]
    if len(set(names)) != len(names):
        raise EmployeeUnavailable("职级顺序尚未正确配置，这次不会扣次数。")
    return {name: level for level, name in enumerate(names, 1)}


def normalize_profile(data, uid):
    """待联调合同：rank_level 越大越高；不根据游戏次数推导职级。"""
    level = data.get("rank_level")
    if (data.get("platform_id") != uid or isinstance(level, bool) or not isinstance(level, int)
            or not isinstance(data.get("rank_name"), str) or not data["rank_name"].strip()
            or data.get("employment_status", "active") != "active"):
        raise EmployeeUnavailable("暂时查不到完整的身份或职级资料，这次不能开始，也不会扣次数。")
    # 部门仅作可选展示，不参与资格判定；缺失或类型异常时保留未知。
    department_id = data.get("department_id")
    department_name = data.get("department_name")
    genders = {"male": "男", "female": "女", "男": "男", "女": "女", "男生": "男", "女生": "女"}
    return {"platform_id": uid, "name": str(data.get("display_name") or uid)[:100],
            "department_id": department_id.strip() if isinstance(department_id, str) else "",
            "department_name": department_name.strip() if isinstance(department_name, str) else "",
            "rank_name": data["rank_name"], "rank_level": level,
            "gender": genders.get(str(data.get("gender") or "unknown").lower(), "未知")}
