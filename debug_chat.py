from dotenv import load_dotenv
load_dotenv()
import os, urllib.request, urllib.parse, json

token = os.environ["BOT_TOKEN"]
chat = os.environ.get("PUBLIC_CHAT_USERNAME", "@peeppo_chat")
ids_to_check = [7972438394, 7785933639, 2028877548]  # Артём, lonely0_oo, Weyzzex1

print(f"checking chat={chat!r}")
for uid in ids_to_check:
    url = "https://api.telegram.org/bot" + token + "/getChatMember?" + urllib.parse.urlencode({"chat_id": chat, "user_id": uid})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:
        body = {"ok": False, "exception": str(e)}
    # never print the token -- it's only ever in the request URL, not the response
    status = body.get("result", {}).get("status") if body.get("ok") else None
    print(uid, "->", "ok=" + str(body.get("ok")), "status=" + str(status), "description=" + str(body.get("description")))
