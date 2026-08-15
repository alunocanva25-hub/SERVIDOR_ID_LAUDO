from __future__ import annotations
import json
from pathlib import Path

path = Path("google-services.json")
if not path.exists():
    raise SystemExit("Coloque google-services.json nesta mesma pasta e execute novamente.")

data = json.loads(path.read_text(encoding="utf-8"))
project = data.get("project_info") or {}
target = None
for client in data.get("client") or []:
    pkg = (((client.get("client_info") or {}).get("android_client_info") or {}).get("package_name") or "")
    if pkg == "br.com.idcamps.idlaudo":
        target = client
        break
if not target:
    raise SystemExit("Não encontrei o pacote br.com.idcamps.idlaudo no google-services.json.")

app_id = ((target.get("client_info") or {}).get("mobilesdk_app_id") or "")
api_keys = target.get("api_key") or []
api_key = (api_keys[0].get("current_key") if api_keys else "") or ""

print("Copie para Render > Environment:")
print("FIREBASE_PROJECT_ID=" + str(project.get("project_id") or ""))
print("FIREBASE_SENDER_ID=" + str(project.get("project_number") or ""))
print("FIREBASE_APP_ID=" + str(app_id))
print("FIREBASE_API_KEY=" + str(api_key))
print()
print("A credencial FIREBASE_SERVICE_ACCOUNT_JSON é separada e deve ser criada em Contas de serviço.")
