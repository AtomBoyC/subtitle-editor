"""Automated unit & integration tests for ListenTo Studio v2.0."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from core.parser import (
    decode_subtitle_bytes,
    export_document_format,
    parse_subtitle_content,
    shift_cues_timestamps,
    split_bilingual_cues,
)
from core.translator import extract_json_payload
from server import app


def _mock_openai_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode("utf-8"))
    user_msg = body["messages"][-1]["content"]
    sys_msg = body["messages"][0]["content"]

    if user_msg == "ping":
        raw_content = json.dumps({"ok": True, "greeting": "pong"})
    elif "scene_summary" in sys_msg and "glossary" in sys_msg and "to_translate" not in user_msg:
        raw_content = json.dumps(
            {
                "scene_summary": "一段关于字幕工作台升级的产品技术演示。",
                "genre": "tech",
                "glossary": [{"source": "ListenTo Studio", "target": "听见 Studio", "note": "产品名"}],
            },
            ensure_ascii=False,
        )
    elif "current_cue" in user_msg:
        payload = json.loads(user_msg)
        cid = payload["current_cue"]["id"]
        raw_content = (
            "<think>Let me polish this line naturally.</think>\n```json\n"
            + json.dumps(
                {
                    "best": f"欢迎使用听见字幕工作台（精修 #{cid}）",
                    "alternatives": ["欢迎来到听见字幕工作室", "大家好，欢迎体验听见 Studio"],
                    "reason": "消除生硬直译，符合中文产品演示习惯",
                },
                ensure_ascii=False,
            )
            + "\n```"
        )
    else:
        payload = json.loads(user_msg)
        items = payload.get("to_translate", [])
        raw_content = (
            "```json\n"
            + json.dumps(
                {
                    "reflection": "注意第3与第4条为跨时间轴长句，按中文语序顺畅衔接。",
                    "translations": [
                        {"id": it["id"], "text": f"地道中文译文 #{it['id']}。"}
                        for it in items
                    ],
                },
                ensure_ascii=False,
            )
            + "\n```"
        )

    envelope = {
        "id": "chatcmpl-mock",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": raw_content}, "finish_reason": "stop"}],
    }
    return httpx.Response(200, json=envelope)


class StudioTestSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_encoding_detection_gb18030_and_utf8(self):
        gbk_bytes = "1\n00:00:01,000 --> 00:00:02,500\n你好，旧版中文字幕\n".encode("gb18030")
        decoded, enc = decode_subtitle_bytes(gbk_bytes, "auto")
        self.assertEqual(enc, "gb18030")
        doc = parse_subtitle_content(decoded, "legacy.srt", enc)
        self.assertEqual(doc.source_language, "zh")
        self.assertEqual(doc.cues[0].source_text, "你好，旧版中文字幕")

    def test_ass_lossless_tags_and_bilingual_export(self):
        ass_path = Path(__file__).resolve().parent.parent / "samples" / "Anime_Dialogue_Styled_JA.ass"
        raw, enc = decode_subtitle_bytes(ass_path.read_bytes(), "auto")
        doc = parse_subtitle_content(raw, ass_path.name, enc)
        self.assertEqual(doc.source_language, "ja")
        self.assertTrue(doc.cues[0].is_special)
        self.assertEqual(doc.cues[1].ass_tags, r"{\an8\pos(960,90)}")

        doc.cues[1].target_text = "第三音乐教室·放学后"
        doc.cues[2].target_text = "你好！真没想到今天也是我第一个到呢"
        rendered_ass = export_document_format(doc, "ass", track_mode="bilingual_tgt_top", ass_secondary_scale=72)
        self.assertIn("Comment: 0,0:00:00.00,0:00:05.00", rendered_ass)
        self.assertIn(r"{\an8\pos(960,90)}第三音乐教室·放学后\N{\fscx72\fscy72", rendered_ass)

        rendered_srt = export_document_format(doc, "srt", track_mode="bilingual_tgt_top")
        self.assertNotIn("OP FX template line", rendered_srt)
        self.assertIn("第三音乐教室·放学后\n第三音楽室・放課後", rendered_srt)

    def test_split_bilingual_and_time_shift(self):
        sample = Path(__file__).resolve().parent.parent / "samples" / "Legacy_Machine_Translated.srt"
        raw, enc = decode_subtitle_bytes(sample.read_bytes(), "auto")
        doc = parse_subtitle_content(raw, sample.name, enc)
        split_cues, count = split_bilingual_cues(doc.cues)
        self.assertEqual(count, 4)
        self.assertEqual(split_cues[0].source_text, "Hello, welcome to our subtitle editor.")
        self.assertIn("欢迎收看", split_cues[0].target_text)

        shifted = shift_cues_timestamps(split_cues, 500)
        self.assertEqual(shifted[0].start_ms, 1623)

    def test_json_extractor_strips_reasoning_and_fences(self):
        messy = "<think>analyzing context...</think>\nHere is JSON:\n```json\n{\"ok\": true, \"items\": [1, 2,]}\n```"
        parsed = extract_json_payload(messy)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["items"], [1, 2])

    def test_end_to_end_api_stream_and_single_rewrite(self):
        orig_async_client = httpx.AsyncClient

        def mocked_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_mock_openai_handler)
            return orig_async_client(*args, **kwargs)

        override = {
            "provider_id": "custom",
            "base_url": "http://localhost:11434/v1",
            "model": "mock-gpt",
            "batch_size": 3,
            "context_window": 2,
        }

        with patch("core.translator.httpx.AsyncClient", side_effect=mocked_client):
            # 1. Test connection endpoint
            t_resp = self.client.post("/api/settings/test", json=override)
            self.assertEqual(t_resp.status_code, 200)
            self.assertTrue(t_resp.json()["ok"])

            # 2. Load sample
            s_resp = self.client.get("/api/samples/tech_keynote")
            self.assertEqual(s_resp.status_code, 200)
            doc = s_resp.json()

            # 3. Extract glossary
            g_resp = self.client.post(
                "/api/ai/extract-glossary",
                json={
                    "cues": doc["cues"],
                    "source_language": "en",
                    "target_language": "zh",
                    "override_settings": override,
                },
            )
            self.assertEqual(g_resp.status_code, 200)
            self.assertEqual(g_resp.json()["glossary"][0]["target"], "听见 Studio")

            # 4. Stream reflective translation
            stream_resp = self.client.post(
                "/api/ai/translate-stream",
                json={
                    "cues": doc["cues"],
                    "source_language": "en",
                    "target_language": "zh",
                    "mode": "reflective",
                    "genre": "tech",
                    "override_settings": override,
                },
            )
            self.assertEqual(stream_resp.status_code, 200)
            events = [
                json.loads(line[5:].strip())
                for line in stream_resp.text.splitlines()
                if line.startswith("data:")
            ]
            event_types = [e["event"] for e in events]
            self.assertIn("start", event_types)
            self.assertIn("batch_done", event_types)
            self.assertEqual(event_types[-1], "complete")
            first_batch_upd = next(e for e in events if e["event"] == "batch_done")["updates"][0]
            self.assertEqual(first_batch_upd["target_text"], "地道中文译文 #1")

            # 5. Single cue AI rewrite
            rw_resp = self.client.post(
                "/api/ai/rewrite-cue",
                json={
                    "cue_id": 1,
                    "cues": doc["cues"],
                    "source_language": "en",
                    "target_language": "zh",
                    "action": "natural",
                    "override_settings": override,
                },
            )
            self.assertEqual(rw_resp.status_code, 200)
            self.assertIn("听见字幕工作台", rw_resp.json()["best"])
            self.assertEqual(len(rw_resp.json()["alternatives"]), 2)

            # 6. Multi-format lossless export
            for upd in events:
                if upd["event"] == "batch_done":
                    for item in upd["updates"]:
                        doc["cues"][item["id"] - 1]["target_text"] = item["target_text"]
            ex_resp = self.client.post(
                "/api/subtitles/export",
                json={
                    "document": doc,
                    "formats": ["srt", "ass", "vtt"],
                    "track_mode": "bilingual_tgt_top",
                    "ass_secondary_scale": 72,
                    "fallback_to_source": True,
                },
            )
            self.assertEqual(ex_resp.status_code, 200)
            self.assertEqual(len(ex_resp.json()["files"]), 3)


if __name__ == "__main__":
    unittest.main()
