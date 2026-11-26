"""Live-server check of forgot/reset/change password and the club badge. Start the app with MAIL_DEBUG_LOG=1 and no SMTP
settings, and pass the server log path: python tests/e2e_accounts.py /tmp/srv.log"""
import re, sys, time
import requests

B, LOG = "http://localhost:8000", sys.argv[1]
H = {"Content-Type": "application/json", "Origin": B}
def sess(): s = requests.Session(); s.headers.update(H); return s
def ok(c, m): print(("PASS " if c else "FAIL ") + m); assert c, m
def link():
    time.sleep(5)
    return re.findall(r"#/reset/([\w\-]+)", open(LOG).read())[-1]

a = sess()
r = a.post(B + "/api/signup", json={"name": "Ada Obi", "email": "ada@t.com", "phone": "08011112222", "password": "oldpassword1", "age_confirmed": True}); ok(r.ok, "signup")
r = a.put(B + "/api/me/badge", json={"club": "Chelsea", "badge_word": "Die-hard", "slogan": "Blue is the colour", "badge_public": True}); ok(r.ok, "save badge")
me = a.get(B + "/api/me").json()["user"]; ok(me["club"] == "Chelsea" and me["slogan"] == "Blue is the colour", "badge returned on /me")
for bad, why in [({"club": "Chelsea", "slogan": "<b>x</b>"}, "html in slogan"), ({"club": "Nope FC"}, "unknown club"), ({"club": "Chelsea", "slogan": "x" * 61}, "long slogan"), ({"club": "Chelsea", "badge_word": "Hater"}, "bad word"), ({"club": "Other", "other_club": ""}, "empty other")]:
    ok(a.put(B + "/api/me/badge", json=bad).status_code == 400, "reject " + why)
ok(a.put(B + "/api/me/badge", json={"club": "Other", "other_club": "Remo Stars FC", "badge_word": "Proud"}).ok, "Other club accepted")
# forgot password
x = sess(); same1 = x.post(B + "/api/forgot", json={"email": "ada@t.com"}).json(); same2 = x.post(B + "/api/forgot", json={"email": "nobody@t.com"}).json()
ok(same1 == same2, "same answer for known and unknown email")
tok = link()
ok(sess().post(B + "/api/reset", json={"token": tok, "password": "short"}).status_code == 400, "reset rejects short password")
ok(sess().post(B + "/api/reset", json={"token": "x" * 40, "password": "newpassword1"}).status_code == 400, "reset rejects bad token")
r = sess().post(B + "/api/reset", json={"token": tok, "password": "newpassword1"}); ok(r.ok, "reset with valid link")
ok(sess().post(B + "/api/reset", json={"token": tok, "password": "another-pass1"}).status_code == 400, "link works only once")
ok(a.get(B + "/api/me").json()["user"] is None, "old sessions signed out after reset")
ok(sess().post(B + "/api/login", json={"email": "ada@t.com", "password": "oldpassword1"}).status_code == 401, "old password rejected")
b = sess(); ok(b.post(B + "/api/login", json={"email": "ada@t.com", "password": "newpassword1"}).ok, "new password works")
# change password
c = sess(); c.post(B + "/api/login", json={"email": "ada@t.com", "password": "newpassword1"})
ok(b.post(B + "/api/me/password", json={"current": "wrong", "new": "thirdpass123"}).status_code == 400, "change needs correct current password")
ok(b.post(B + "/api/me/password", json={"current": "newpassword1", "new": "x" * 51}).status_code == 400, "change enforces max length")
ok(b.post(B + "/api/me/password", json={"current": "newpassword1", "new": "thirdpass123"}).ok, "change password")
ok(c.get(B + "/api/me").json()["user"] is None and b.get(B + "/api/me").json()["user"], "other devices signed out, this one stays")
# expiry
sess().post(B + "/api/forgot", json={"email": "ada@t.com"}); tok = link()
import sqlite3; con = sqlite3.connect("/tmp/fmd6/t.db"); con.execute("UPDATE password_resets SET expires_at=1"); con.commit()
ok(sess().post(B + "/api/reset", json={"token": tok, "password": "fourthpass12"}).status_code == 400, "expired link rejected")
# admin switch
ad = sess(); ad.post(B + "/api/login", json={"email": "admin@millionairefans.local", "password": "AdminPass#12345"})
ok(ad.put(B + "/api/admin/settings", json={"forgot_password_enabled": 0}).ok, "admin switches reset off")
ok(sess().post(B + "/api/forgot", json={"email": "ada@t.com"}).status_code == 403, "reset refused when off")
ok(sess().get(B + "/api/me").json()["forgot_enabled"] is False, "login page told to hide the link")
ad.put(B + "/api/admin/settings", json={"forgot_password_enabled": 1})
em = ad.get(B + "/api/admin/email").json(); ok(em["configured"] is False and em["counts"].get("skipped", 0) >= 3, "admin email page shows skipped (no SMTP)")
print("ALL OK")
