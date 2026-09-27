import io, json, shutil, time

p = "/app/config/config.json"
bak = p + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(p, bak)
s = io.open(p, encoding="utf-8").read()
old = '"key": "weisi_rose",\n      "name": "薇丝-bot"'
new = '"key": "weisi_rose",\n      "name": "薇丝"'
n = s.count(old)
print("匹配旧名字的次数:", n)
if n != 1:
    raise SystemExit("没匹配到（或不止一处），未做任何修改")
s2 = s.replace(old, new)
cfg = json.loads(s2)                      # 校验仍是合法 JSON
io.open(p, "w", encoding="utf-8").write(s2)
print("已写入；备份:", bak)
for b in json.load(io.open(p, encoding="utf-8"))["bots"]:
    print("  Bot key={} name={!r} short={!r} uid={}".format(
        b["key"], b["name"], b.get("short"), b.get("uid")))
