"""Тест соответствия ТЗ, сгенерированный из условия, а не из тестов студента.
ТЗ: POST /predict, 7 полей, ответ — ОДНО булево значение.
Логика: verified => без нарушений; unverified => без нарушений только при наличии изображений."""
import sys, json
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from main import app
c = TestClient(app)

BASE = {"seller_id":1,"is_verified_seller":True,"item_id":10,"name":"N",
        "description":"D","category":1,"images_qty":0}

paths = sorted({r.path for r in app.routes if "predict" in getattr(r,"path","")})
# найти рабочий путь
found = None
for p in paths or ["/predict"]:
    if c.post(p, json=BASE).status_code != 404:
        found = p; break
if not found:
    for p in ["/predict","/advertisement/predict"]:
        if c.post(p, json=BASE).status_code != 404: found=p; break

res = {"declared_paths": paths, "working_path": found,
       "path_matches_spec": found == "/predict"}

def call(**kw):
    d = dict(BASE); d.update(kw)
    r = c.post(found, json=d)
    return r.status_code, (r.json() if r.headers.get("content-type","").startswith("application/json") else None)

s1, b1 = call(is_verified_seller=True,  images_qty=0)   # verified -> нарушений нет
s2, b2 = call(is_verified_seller=False, images_qty=3)   # unverified + images -> нарушений нет
s3, b3 = call(is_verified_seller=False, images_qty=0)   # unverified без images -> нарушение

res["response_is_bare_bool"] = isinstance(b1, bool)
res["raw_responses"] = {"verified": b1, "unverified_with_images": b2, "unverified_no_images": b3}

# извлечь булево независимо от обёртки
def val(b):
    if isinstance(b, bool): return b
    if isinstance(b, dict) and len(b)==1: return list(b.values())[0]
    return None
v1,v2,v3 = val(b1),val(b2),val(b3)
# логика верна, если 1 и 2 совпадают, а 3 им противоположно
res["logic_correct"] = (v1 is not None and v1==v2 and v3 == (not v1))
res["statuses"] = [s1,s2,s3]

# ошибка валидации
sv,_ = call(seller_id="oops")
res["validation_status"] = sv
res["validation_is_4xx"] = 400 <= sv < 500
print(json.dumps(res, ensure_ascii=False))
