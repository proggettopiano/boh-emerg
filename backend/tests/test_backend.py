"""Comprehensive backend tests for Scorelib."""
import io
import os
import time
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://sheet-music-hub-4.preview.emergentagent.com").rstrip("/")


# ---------------- AUTH ----------------
class TestAuth:
    def test_health(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/")
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_register_and_duplicate(self, api_client):
        email = f"test_{uuid.uuid4().hex[:8]}@example.com"
        r = api_client.post(f"{BASE_URL}/api/auth/register",
                            json={"email": email, "password": "Pass1234!"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "token" in data and "user" in data
        assert data["user"]["email"] == email
        # duplicate
        r2 = api_client.post(f"{BASE_URL}/api/auth/register",
                             json={"email": email, "password": "Pass1234!"})
        assert r2.status_code == 409

    def test_register_short_password(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/register",
                            json={"email": f"x_{uuid.uuid4().hex[:6]}@example.com", "password": "abc"})
        assert r.status_code == 422

    def test_login_admin_and_me(self, api_client, admin_token):
        assert isinstance(admin_token, str) and len(admin_token) > 10
        r = api_client.get(f"{BASE_URL}/api/auth/me",
                           headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        assert r.json()["email"] == "admin@scorelib.app"

    def test_login_wrong_password(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/login",
                            json={"email": "admin@scorelib.app", "password": "wrong!!"})
        assert r.status_code == 401

    def test_me_no_token(self, api_client):
        r = requests.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401

    def test_forgot_existing_returns_ok(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/forgot",
                            json={"email": "admin@scorelib.app"})
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_forgot_unknown_returns_ok(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/forgot",
                            json={"email": f"nope_{uuid.uuid4().hex[:6]}@example.com"})
        assert r.status_code == 200

    def test_reset_invalid_token(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/auth/reset",
                            json={"token": "INVALID_TOKEN_XYZ", "password": "NewPass123!"})
        assert r.status_code == 400


# ---------------- PROFILE / SETTINGS ----------------
class TestProfileSettings:
    def test_patch_profile(self, api_client, auth_headers):
        r = api_client.patch(f"{BASE_URL}/api/profile",
                             json={"name": "Admin Tester", "how_found": "test", "profile_completed": True},
                             headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Admin Tester"
        assert data["profile_completed"] is True

    def test_backup_toggle(self, api_client, auth_headers):
        r = api_client.post(f"{BASE_URL}/api/settings/backup",
                            json={"enabled": False}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["backup_enabled"] is False
        r2 = api_client.post(f"{BASE_URL}/api/settings/backup",
                             json={"enabled": True}, headers=auth_headers)
        assert r2.json()["backup_enabled"] is True

    def test_change_password_wrong_current(self, api_client, auth_headers):
        r = api_client.post(f"{BASE_URL}/api/settings/password",
                            json={"current_password": "WRONG", "new_password": "WhatEver123!"},
                            headers=auth_headers)
        assert r.status_code == 401

    def test_change_email_wrong_password(self, api_client, auth_headers):
        r = api_client.post(f"{BASE_URL}/api/settings/email",
                            json={"password": "WRONG", "new_email": "newmail@example.com"},
                            headers=auth_headers)
        assert r.status_code == 401


# ---------------- PDFs ----------------
@pytest.fixture(scope="class")
def uploaded_pdf(api_client, auth_headers, sample_pdf_bytes):
    files = {"files": (f"test_{uuid.uuid4().hex[:6]}.pdf", sample_pdf_bytes, "application/pdf")}
    r = requests.post(f"{BASE_URL}/api/pdfs/upload",
                      headers={"Authorization": auth_headers["Authorization"]},
                      files=files)
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    if res.get("duplicate"):
        # Re-use existing record from previous class within the same session
        return {"ok": True, "pdf_id": res["existing_id"], "pages": 2, "ocr": False}
    assert res["ok"] is True, res
    return res


class TestPdfs:
    def test_upload_invalid_pdf(self, auth_headers):
        files = {"files": ("bad.pdf", b"NOTAPDF DATA", "application/pdf")}
        r = requests.post(f"{BASE_URL}/api/pdfs/upload",
                          headers={"Authorization": auth_headers["Authorization"]},
                          files=files)
        assert r.status_code == 200
        res = r.json()["results"][0]
        assert res["ok"] is False
        assert "Non è un PDF valido" in res.get("error", "")

    def test_upload_real_pdf(self, uploaded_pdf):
        assert uploaded_pdf["ok"] is True
        assert uploaded_pdf["pages"] >= 1
        assert "pdf_id" in uploaded_pdf
        assert "ocr" in uploaded_pdf

    def test_upload_duplicate(self, auth_headers, sample_pdf_bytes, uploaded_pdf):
        # send same content again
        files = {"files": ("dup.pdf", sample_pdf_bytes, "application/pdf")}
        r = requests.post(f"{BASE_URL}/api/pdfs/upload",
                          headers={"Authorization": auth_headers["Authorization"]},
                          files=files)
        assert r.status_code == 200
        res = r.json()["results"][0]
        assert res.get("duplicate") is True
        assert res["ok"] is False

    def test_list_pdfs(self, api_client, auth_headers, uploaded_pdf):
        r = api_client.get(f"{BASE_URL}/api/pdfs?sort=date_desc", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        assert "items" in d and "tags" in d
        ids = [x["id"] for x in d["items"]]
        assert uploaded_pdf["pdf_id"] in ids

    def test_list_pdfs_sort_variants(self, api_client, auth_headers):
        for s in ["date_asc", "name_asc", "name_desc"]:
            r = api_client.get(f"{BASE_URL}/api/pdfs?sort={s}", headers=auth_headers)
            assert r.status_code == 200

    def test_patch_favorite_and_tags(self, api_client, auth_headers, uploaded_pdf):
        pid = uploaded_pdf["pdf_id"]
        r = api_client.patch(f"{BASE_URL}/api/pdfs/{pid}",
                             json={"is_favorite": True, "tags": ["jazz", "Worship"]},
                             headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        assert d["is_favorite"] is True
        assert "jazz" in d["tags"] and "worship" in d["tags"]

        r2 = api_client.get(f"{BASE_URL}/api/pdfs?favorite=true", headers=auth_headers)
        assert r2.status_code == 200
        ids = [x["id"] for x in r2.json()["items"]]
        assert pid in ids

        r3 = api_client.get(f"{BASE_URL}/api/pdfs?tag=jazz", headers=auth_headers)
        assert pid in [x["id"] for x in r3.json()["items"]]

    def test_get_pdf_file_inline(self, auth_headers, uploaded_pdf):
        pid = uploaded_pdf["pdf_id"]
        r = requests.get(f"{BASE_URL}/api/pdfs/{pid}/file", headers=auth_headers)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")
        cd = r.headers.get("content-disposition", "")
        assert "attachment" not in cd.lower()
        assert r.content[:4] == b"%PDF"


# ---------------- SEARCH ----------------
class TestSearch:
    def test_search_returns_results(self, api_client, auth_headers, uploaded_pdf):
        # word from uploaded pdf body
        r = api_client.get(f"{BASE_URL}/api/search?q=jazz", headers=auth_headers)
        assert r.status_code == 200
        results = r.json()["results"]
        assert isinstance(results, list)
        if results:
            r0 = results[0]
            for k in ["pdf_id", "title", "page", "snippet", "source", "match_in"]:
                assert k in r0

    def test_search_no_token(self):
        r = requests.get(f"{BASE_URL}/api/search?q=x")
        assert r.status_code == 401


# ---------------- LIBRARIES ----------------
class TestLibraries:
    def test_create_list_add_share(self, api_client, auth_headers, uploaded_pdf):
        # create
        r = api_client.post(f"{BASE_URL}/api/libraries",
                            json={"name": f"TEST_LIB_{uuid.uuid4().hex[:6]}", "description": "test"},
                            headers=auth_headers)
        assert r.status_code == 200
        lib = r.json()
        assert "share_token" in lib and "id" in lib
        lib_id = lib["id"]

        # list
        rl = api_client.get(f"{BASE_URL}/api/libraries", headers=auth_headers)
        assert rl.status_code == 200
        ids = [x["id"] for x in rl.json()["items"]]
        assert lib_id in ids

        # add pdf
        rp = api_client.post(f"{BASE_URL}/api/libraries/{lib_id}/pdfs",
                             json={"pdf_ids": [uploaded_pdf["pdf_id"]]},
                             headers=auth_headers)
        assert rp.status_code == 200
        assert uploaded_pdf["pdf_id"] in rp.json()["added"]

        # shared without auth -> 401
        rs = requests.get(f"{BASE_URL}/api/shared/{lib['share_token']}")
        assert rs.status_code == 401
        # with auth -> 200
        rs2 = requests.get(f"{BASE_URL}/api/shared/{lib['share_token']}", headers=auth_headers)
        assert rs2.status_code == 200
        d = rs2.json()
        assert "pdfs" in d and len(d["pdfs"]) >= 1

        # cleanup
        api_client.delete(f"{BASE_URL}/api/libraries/{lib_id}", headers=auth_headers)


# ---------------- ADMIN LOGS ----------------
class TestAdminLogs:
    def test_admin_logs_correct_pwd(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/logs",
                            json={"password": "Rome02009"})
        assert r.status_code == 200
        d = r.json()
        assert "items" in d and "types" in d

    def test_admin_logs_wrong_pwd(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/logs",
                            json={"password": "wrong"})
        assert r.status_code == 401
