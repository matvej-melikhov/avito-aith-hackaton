#!/usr/bin/env python3
"""Read study outcomes through public APIs; keep sessions only in memory."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import urllib.request

BASE = "http://127.0.0.1:18010/api"

def client(identity):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    req = urllib.request.Request(BASE + "/v1/auth/local/login", data=json.dumps({"identity": identity}).encode(), headers={"Content-Type": "application/json"})
    with opener.open(req) as response:
        json.load(response)
    def get(route):
        with opener.open(BASE + route) as response:
            return json.load(response)
    return get

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    args = parser.parse_args()
    root = Path.cwd()
    manifest = json.loads((root / ".cache/usability/scenarios.json").read_text())
    if manifest["marker"] != "workspace-usability-v1" or manifest["api_url"] != "http://127.0.0.1:18010":
        raise RuntimeError("Not the isolated study")
    staff = client("coordinator")
    catalog = staff("/v2/catalog")
    outcomes = {}
    for case in manifest["scenarios"]:
        code=case["code"]
        value={"actor":case["actor"],"initial_state":case["initial_state"]}
        sid=case.get("submission_id")
        if code.startswith("S"):
            own=client(case["actor"])
            context=own(f"/v2/course-run-homeworks/{case['publication_id']}/student-context")
            sid=context.get("submission_id")
            value.update({"context_accessible":True,"submission_id":sid,"self_reviews":[{"status":r["status"],"disposition":r.get("disposition")} for r in context["self_reviews"]],"quota":context["quota"]})
        if sid:
            submission=staff(f"/v2/submissions/{sid}")
            value["attempts"]=[{k:a.get(k) for k in ("id","sequence","comment","status","artifact_id")} for a in submission["attempts"]]
            value["reviews"]=[{k:r.get(k) for k in ("id","iteration_id","submission_version_id","score","decision","revision_deadline")} for r in submission["reviews"]]
            value["current_publication_id"]=submission["current_publication_id"]
        if code=="C1":
            runs=[r for r in catalog["course_runs"] if r["course_id"]==case["course_id"]]
            value["runs"]=[{k:r.get(k) for k in ("id","title","starts_at","ends_at")} for r in runs]
            value["homeworks"]=staff(f"/v2/courses/{case['course_id']}/homeworks")["items"]
            value["published"]={r["id"]:staff(f"/v1/course-runs/{r['id']}/homeworks")["items"] for r in runs}
        if code=="C2":
            value["assignments"]=staff(f"/v2/course-runs/{case['course_run_id']}/assignments")["items"]
        outcomes[code]=value
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(outcomes,ensure_ascii=False,indent=2)+"\n")
    print(target)

if __name__=="__main__":main()
