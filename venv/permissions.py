from typing import List, Dict, Any

# предполагаем, что CONFIG импортируется сверху из главного места
# from your_module import CONFIG


def is_global_admin(user_id: int, CONFIG: Dict[str, Any]) -> bool:
    return int(user_id) in set(map(int, CONFIG.get("admins", [])))


def get_admin_projects(user_id: int, CONFIG: Dict[str, Any]) -> List[str]:
    """Список проектов, которыми может управлять пользователь."""
    if is_global_admin(user_id, CONFIG):
        return [p["project_name"] for p in CONFIG.get("projects", [])]
    res = []
    for p in CONFIG.get("projects", []):
        if int(user_id) in set(map(int, p.get("project_admin_ids", []))):
            res.append(p["project_name"])
    return res


def can_admin_project(user_id: int, project_name: str, CONFIG: Dict[str, Any]) -> bool:
    if is_global_admin(user_id, CONFIG):
        return True
    for p in CONFIG.get("projects", []):
        if p.get("project_name") == project_name:
            return int(user_id) in set(map(int, p.get("project_admin_ids", [])))
    return False


def find_project(CONFIG: Dict[str, Any], project_name: str) -> Dict[str, Any] | None:
    return next((p for p in CONFIG.get("projects", []) if p.get("project_name") == project_name), None)
