"""Stats dashboard root-cause fixes, 2026-10-14.

The owner's report: "no historical data, the GitHub stats are not showing
properly, the number of users estimate is not correct." Each class below pins
one root cause, against the code path that produced the wrong number:

  - FetcherWritesAppendOnly      history is never re-serialised; one bad line
                                 is no longer silently dropped
  - FetcherReleasesPaginated     >100 releases no longer drop the oldest
  - FetcherRateFloorScaled       the 30/min Search bucket no longer costs 62 s
  - ArchiveIsServedWhole         all-time tiles no longer change with the range
  - FetchWritesWhereTheDashboardReads
  - ReadOnlyStatsMode            serve another install's archive, never write it
  - OwnerExclusion               test_rig was on 0 events; ids + fingerprints
  - TestRigMarker                ~/.phosphene-test-rig tags every local install
  - OneWeeklyActiveDefinition    tile == last point of the weekly curve
  - UserNumbers                  installs that rendered vs installs that opened
  - PackLossesPerInstall         "H3 went missing 8x" -> per install, by outcome
  - FleetCacheSchema             old-shape cache is refetched, not served
  - PageContract / PageJs        labels, and the JS series/delta helpers run in node

No network: every PostHog / GitHub call is mocked. conftest.py sandboxes state.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import mlx_ltx_panel as p

ROOT = Path(p.__file__).resolve().parent
HTML = (ROOT / "panel_assets" / "stats.html").read_text(encoding="utf-8")


def _load_fetcher():
    """Import scripts/fetch_repo_stats.py with a dummy token (it exits at
    import without one) — no request is ever made by these tests."""
    spec = importlib.util.spec_from_file_location(
        "fetch_repo_stats_t", ROOT / "scripts" / "fetch_repo_stats.py")
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"GH_STATS_TOKEN": "dummy-not-a-token"}):
        spec.loader.exec_module(mod)
    return mod


F = _load_fetcher()


def _row(date, **kw):
    r = {"date": date, "fetched_at": f"{date}T10:00:00Z", "stars": 1, "forks": 1,
         "clones_window": [{"date": date, "count": 5, "uniques": 4}],
         "stars_timeline": [{"date": date, "count": 1}], "open_items": []}
    r.update(kw)
    return r


class FetcherWritesAppendOnly(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "stats-data.jsonl"

    def test_new_day_appends_and_keeps_every_earlier_byte(self):
        # Including a line that is not valid JSON: the old writer parsed and
        # re-dumped the file and DROPPED it.
        history = (b'{"date": "2026-09-28", "x": 1}\n'
                   b'{not json at all\n'
                   b'{"date":"2026-09-29","spacing":"odd"}\n')
        self.path.write_bytes(history)
        F._write_row(self.path, _row("2026-10-14"))
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(history))
        self.assertEqual(json.loads(data[len(history):])["date"], "2026-10-14")

    def test_missing_trailing_newline_is_repaired_not_glued(self):
        self.path.write_bytes(b'{"date": "2026-09-29"}')
        F._write_row(self.path, _row("2026-10-14"))
        lines = self.path.read_bytes().splitlines()
        self.assertEqual(lines[0], b'{"date": "2026-09-29"}')
        self.assertEqual(json.loads(lines[1])["date"], "2026-10-14")

    def test_same_day_rerun_replaces_only_the_last_line(self):
        history = b'{"date": "2026-09-29", "keep": "exact   spacing"}\n'
        self.path.write_bytes(history)
        F._write_row(self.path, _row("2026-10-14", stars=250))
        F._write_row(self.path, _row("2026-10-14", stars=256))
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(history))
        lines = data.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["stars"], 256)

    def test_first_write_creates_the_file(self):
        F._write_row(self.path, _row("2026-10-14"))
        self.assertEqual(len(self.path.read_bytes().splitlines()), 1)


class FetcherReleasesPaginated(unittest.TestCase):
    def test_all_pages_feed_both_release_views(self):
        rels = [{"tag_name": f"v1.{i}", "published_at": f"2026-01-01T00:00:{i % 60:02d}Z",
                 "assets": []} for i in range(130)]
        rels.append({"tag_name": "weights-q4", "published_at": "2026-02-01T00:00:00Z",
                     "assets": [{"name": "q4_25__a", "download_count": 7},
                                {"name": "q4_25__b", "download_count": 9}]})
        F._RELEASES_CACHE.clear()
        with mock.patch.object(F, "_get_paginated", return_value=rels) as gp:
            tl = F.fetch_release_timeline("o/r")
            totals = F.fetch_release_downloads("o/r")
        self.assertEqual(gp.call_count, 1)  # one walk, shared
        self.assertNotIn("per_page", gp.call_args[0][0])  # _get_paginated adds it
        self.assertEqual(len(tl), 131)
        self.assertEqual(len(totals["by_release"]), 131)
        self.assertEqual(totals["total"], 16)
        F._RELEASES_CACHE.clear()


class FetcherRateFloorScaled(unittest.TestCase):
    def test_search_bucket_does_not_sleep_with_most_of_it_left(self):
        with mock.patch.object(F.time, "sleep") as sl:
            F._handle_rate_limit({"X-RateLimit-Remaining": "29", "X-RateLimit-Limit": "30",
                                  "X-RateLimit-Reset": str(int(time.time()) + 60)})
        sl.assert_not_called()

    def test_a_nearly_empty_core_bucket_still_waits(self):
        with mock.patch.object(F.time, "sleep") as sl:
            F._handle_rate_limit({"X-RateLimit-Remaining": "50", "X-RateLimit-Limit": "5000",
                                  "X-RateLimit-Reset": str(int(time.time()) + 5)})
        sl.assert_called_once()


def _isolate_archive(tc):
    """Point the panel's archive + fleet cache at a temp dir for one test.
    The 121-row synthetic archives found in 12 worktrees' state/ on
    2026-10-14 were this suite's fixtures, written wherever STATE_DIR
    happened to resolve — in a checkout whose state/ is the maintainer's
    drive, that is the real archive."""
    d = Path(tempfile.mkdtemp())
    for name, fn in (("STATS_DATA_FILE", "stats-data.jsonl"),
                     ("USAGE_FLEET_CACHE", "usage-fleet.json")):
        pt = mock.patch.object(p, name, d / fn)
        pt.start()
        tc.addCleanup(pt.stop)


class ArchiveIsServedWhole(unittest.TestCase):
    def setUp(self):
        _isolate_archive(self)
        rows = [_row("2026-05-22"), _row("2026-09-29"),
                _row("2026-09-29", stars=240), _row("2026-10-14", stars=256)]
        p.STATS_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        p.STATS_DATA_FILE.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                     encoding="utf-8")
        p._STATS_DATA_RANGE_CACHE.clear()
        self.addCleanup(p._STATS_DATA_RANGE_CACHE.clear)

    def _rows(self, rk):
        return [json.loads(l) for l in p._stats_data_for_range(rk).splitlines() if l.strip()]

    def test_same_rows_for_every_range_even_when_the_archive_is_stale(self):
        # A 7-day range on an archive whose newest row is weeks old used to
        # come back EMPTY; the stale rows are what the page must show.
        for rk in ("7d", "30d", "90d", "all"):
            self.assertEqual([r["date"] for r in self._rows(rk)],
                             ["2026-05-22", "2026-09-29", "2026-10-14"], rk)

    def test_duplicate_date_keeps_the_later_line(self):
        self.assertEqual(self._rows("all")[1]["stars"], 240)

    def test_older_rows_are_slimmed_the_newest_is_whole(self):
        rows = self._rows("all")
        self.assertNotIn("stars_timeline", rows[0])
        self.assertNotIn("open_items", rows[0])
        self.assertIn("clones_window", rows[0])   # per-day facts survive
        self.assertIn("stars_timeline", rows[-1])
        self.assertIn("open_items", rows[-1])

    def test_cache_has_no_date_dependence(self):
        a = p._stats_data_for_range("7d")
        with mock.patch.object(p.time, "time", return_value=time.time() + 5 * 86400):
            b = p._stats_data_for_range("7d")
        self.assertEqual(a, b)


class FetchWritesWhereTheDashboardReads(unittest.TestCase):
    def test_fetch_subprocess_gets_the_archive_path(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw.get("env") or {})
            return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

        with mock.patch.object(p, "_resolve_github_token", return_value="t"), \
             mock.patch.object(p, "STATS_READONLY", False), \
             mock.patch.object(p.subprocess, "run", side_effect=fake_run):
            p._run_stats_fetch_once()
        self.assertEqual(seen.get("PHOSPHENE_STATS_OUT"), str(p.STATS_DATA_FILE))


class ReadOnlyStatsMode(unittest.TestCase):
    def test_no_fetch_in_read_only_mode(self):
        with mock.patch.object(p, "_resolve_github_token", return_value="t"), \
             mock.patch.object(p, "STATS_READONLY", True), \
             mock.patch.object(p.subprocess, "run") as run:
            p._run_stats_fetch_once()
        run.assert_not_called()

    def test_refresh_route_refuses(self):
        import panel.routes_stats as rs
        rs.P = p
        h = mock.Mock()
        with mock.patch.object(p, "STATS_READONLY", True), \
             mock.patch.object(rs.threading, "Thread") as th:
            rs.stats_refresh(h, "/stats/refresh", {}, "")
        th.assert_not_called()
        self.assertEqual(h._json.call_args[0][1], 409)

    def test_source_dir_env_points_reads_at_the_other_install(self):
        src = tempfile.mkdtemp()
        code = ("import mlx_ltx_panel as p, json; print(json.dumps([str(p.STATS_DATA_FILE), "
                "str(p.USAGE_LOG_READ_FILE), str(p.USAGE_LOG_FILE), p.STATS_READONLY]))")
        env = dict(os.environ, PHOSPHENE_STATS_SOURCE_DIR=src,
                   LTX_STATE_DIR=tempfile.mkdtemp())
        out = subprocess.run([os.sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=120)
        data, read_log, write_log, ro = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertTrue(data.startswith(src))
        self.assertTrue(read_log.startswith(src))
        self.assertFalse(write_log.startswith(src))  # capture never appends there
        self.assertTrue(ro)


class OwnerExclusion(unittest.TestCase):
    def setUp(self):
        self.f = Path(tempfile.mkdtemp()) / "owner.json"
        patcher = mock.patch.object(p, "ANALYTICS_OWNER_FILE", self.f)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_file_no_clause(self):
        self.assertEqual(p._owner_exclusion_sql(), "")

    def test_ids_and_fingerprints_reach_every_fleet_query(self):
        oid = "3faade2b-aa7f-45a5-8793-f2b3d2ede6e8"
        self.f.write_text(json.dumps({
            "install_ids": [oid, "not-a-uuid", "x'); DROP"],
            "fingerprints": [{"country": "IL", "chip_family": "M4 Max", "ram_gb": 64},
                             {"country": "I'L"}]}))
        clause = p._owner_exclusion_sql()
        self.assertIn(f"distinct_id NOT IN ('{oid}')", clause)
        self.assertIn("properties['$geoip_country_code'] = 'IL'", clause)
        self.assertIn("properties['ram_gb'] = 64", clause)
        self.assertNotIn("DROP", clause)
        self.assertNotIn("I'L", clause)
        for rk in ("7d", "all"):
            for name, q in p._fleet_queries(rk).items():
                self.assertIn(oid, q, name)

    def test_report_says_whether_the_owner_is_excluded(self):
        self.f.write_text(json.dumps({"install_ids": ["3faade2b-aa7f-45a5-8793-f2b3d2ede6e8"]}))
        with mock.patch.object(p, "_analytics_query_key", return_value="k"), \
             mock.patch.object(p, "_usage_fleet_query_one", return_value=[]):
            rep = p._usage_fleet_report("7d")
        # every query "succeeded" with no rows -> a report, flagged
        self.assertTrue(rep["owner_excluded"])


class TestRigMarker(unittest.TestCase):
    def test_marker_file_tags_and_env_zero_overrides(self):
        marker = Path(tempfile.mkdtemp()) / ".phosphene-test-rig"
        with mock.patch.object(p, "ANALYTICS_TEST_RIG_MARKER", marker), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PHOSPHENE_TEST_RIG", None)
            self.assertFalse(p._analytics_is_test_rig())
            marker.touch()
            self.assertTrue(p._analytics_is_test_rig())
            os.environ["PHOSPHENE_TEST_RIG"] = "0"
            self.assertFalse(p._analytics_is_test_rig())
            os.environ.pop("PHOSPHENE_TEST_RIG")


def _canned(range_key: str) -> dict:
    """Canned HogQL results keyed by query name."""
    return {
        "wau": [[999]],  # deliberately different: the tile must use the curve
        "dau": [[80]],
        "active_in_range": [[1103 if range_key != "7d" else 998]],
        "active_in_range_prior": [[1000]],
        "total_renders": [[50461]],
        "total_installs": [[2402]],
        "rendered_installs": [[1428]],
        "installs_by_day": [["2026-08-09", 10], ["2026-10-14", 3]],
        "active_by_week": [[12, 5], [2, 421], [1, 343], [0, 326]],
        "pack_losses": [["h3", "a", "true", "2026-10-09T08:42:00Z"],
                        ["h3", "b", "false", "2026-10-09T20:26:00Z"],
                        ["h3", "c", "false", "2026-10-07T23:39:00Z"]],
        "pack_loss_last_boot": [["a", "2026-10-10T18:00:00Z"],
                                ["b", "2026-10-12T09:00:00Z"],
                                ["c", "2026-10-07T23:39:10Z"]],
    }


def _fake_query(range_key):
    canned = _canned(range_key)
    queries = p._fleet_queries(range_key)
    by_sql = {sql: canned.get(name, []) for name, sql in queries.items()}
    return lambda sql, key: by_sql.get(sql, [])


class OneWeeklyActiveDefinition(unittest.TestCase):
    def _report(self, rk):
        with mock.patch.object(p, "_analytics_query_key", return_value="k"), \
             mock.patch.object(p, "_usage_fleet_query_one", side_effect=_fake_query(rk)):
            return p._usage_fleet_report(rk)

    def test_tile_equals_the_last_point_of_the_curve(self):
        rep = self._report("30d")
        weeks = rep["growth"]["active_by_week"]
        self.assertEqual(rep["tiles"]["weekly_active_installs"], 326)
        self.assertEqual(weeks[-1]["installs"], 326)
        self.assertEqual([w["installs"] for w in weeks], [421, 343, 326])  # k=12 dropped

    def test_seven_day_active_is_the_same_number(self):
        self.assertEqual(self._report("7d")["tiles"]["active_in_range"], 326)

    def test_windows_are_labelled_by_their_end_date(self):
        now = time.mktime((2026, 10, 14, 12, 0, 0, 0, 0, 0)) - time.timezone
        out = p._usage_rolling_weeks([[1, 343], [0, 326], [13, 1]], now=now)
        self.assertEqual(out, [("2026-10-07", 343), ("2026-10-14", 326)])


class NoSilentRowCap(unittest.TestCase):
    def test_multi_row_series_ask_for_more_than_hogqls_default_100(self):
        for rk in ("7d", "all"):
            q = p._fleet_queries(rk)
            for name in ("installs_by_day", "boots_by_day", "renders_by_day",
                         "pack_losses", "pack_loss_last_boot"):
                self.assertIn("LIMIT 10000", q[name], f"{name} ({rk})")


class UserNumbers(unittest.TestCase):
    def test_rendered_and_never_rendered(self):
        with mock.patch.object(p, "_analytics_query_key", return_value="k"), \
             mock.patch.object(p, "_usage_fleet_query_one", side_effect=_fake_query("30d")):
            t = p._usage_fleet_report("30d")["tiles"]
        self.assertEqual(t["total_installs"], 2402)
        self.assertEqual(t["rendered_installs"], 1428)
        self.assertEqual(t["never_rendered_installs"], 974)

    def test_local_mirror_has_the_same_fields(self):
        t = p._usage_local_report("7d")["tiles"]
        for k in ("rendered_installs", "never_rendered_installs", "total_installs"):
            self.assertIn(k, t)


class PackLossesPerInstall(unittest.TestCase):
    def test_classification(self):
        out = p._usage_pack_loss_summary(_canned("7d")["pack_losses"],
                                         _canned("7d")["pack_loss_last_boot"])
        self.assertEqual(out["h3"], {"installs": 3, "recovered": 1, "still_missing": 1,
                                     "not_seen_since": 1, "unconfirmed": 0})

    def test_failed_last_boot_query_is_never_guessed(self):
        out = p._usage_pack_loss_summary(_canned("7d")["pack_losses"], None)
        self.assertEqual(out["h3"]["unconfirmed"], 2)
        self.assertEqual(out["h3"]["still_missing"], 0)

    def test_local_mirror_counts_a_recovered_loss(self):
        recs = [{"event": "app_boot", "ts": 100.0, "props": {}},
                {"event": "pack_state_change", "ts": 100.0,
                 "props": {"pack": "h3", "from": True, "to": False}},
                {"event": "app_boot", "ts": 500.0, "props": {}},
                {"event": "pack_state_change", "ts": 500.0,
                 "props": {"pack": "h3", "from": False, "to": True}}]
        with mock.patch.object(p, "_usage_log_read", return_value=recs), \
             mock.patch.object(p.time, "time", return_value=1000.0):
            rep = p._usage_local_report("all")
        self.assertEqual(rep["pack_flips"]["losses"]["h3"]["recovered"], 1)


class FleetCacheSchema(unittest.TestCase):
    def setUp(self):
        _isolate_archive(self)

    def test_old_shape_cache_is_not_served(self):
        old = {"7d": {"ok": True, "source": "fleet", "tiles": {"total_installs": 2453},
                      "_fetched_at": time.time()}}
        p.USAGE_FLEET_CACHE.parent.mkdir(parents=True, exist_ok=True)
        p.USAGE_FLEET_CACHE.write_text(json.dumps(old))
        fresh = {"ok": True, "source": "fleet", "tiles": {"total_installs": 2402}}
        with mock.patch.object(p, "_analytics_query_key", return_value="k"), \
             mock.patch.object(p, "_usage_fleet_report", return_value=dict(fresh)) as fr:
            rep = p._usage_report(range_key="7d")
        fr.assert_called_once()
        self.assertEqual(rep["tiles"]["total_installs"], 2402)
        self.assertEqual(json.loads(p.USAGE_FLEET_CACHE.read_text())["7d"]["_schema"],
                         p.USAGE_FLEET_SCHEMA)


class PageContract(unittest.TestCase):
    def test_no_made_up_divisor_and_no_today_label(self):
        self.assertNotIn("/ 1.7", HTML)
        self.assertNotIn("Clones · today", HTML)
        self.assertNotIn("Views · today", HTML)

    def test_user_numbers_are_named_once(self):
        self.assertIn("'Installs that rendered'", HTML)
        self.assertIn("'Installs that opened the app'", HTML)
        self.assertIn("'Weekly active installs'", HTML)
        self.assertIn("t.rendered_installs", HTML)

    def test_source_only_releases_and_stale_warning(self):
        self.assertIn("source-only", HTML)
        self.assertIn("stale-warn", HTML)
        self.assertIn("still missing after reopening", HTML)


def _js_functions(*names: str) -> str:
    """Pull top-level `function name(...) {...}` bodies out of stats.html by
    brace matching — the real code, not a copy."""
    out = []
    for n in names:
        i = HTML.index(f"function {n}(")
        j = HTML.index("{", i)
        depth = 0
        for k in range(j, len(HTML)):
            if HTML[k] == "{":
                depth += 1
            elif HTML[k] == "}":
                depth -= 1
                if depth == 0:
                    out.append(HTML[i:k + 1])
                    break
    return "\n".join(out)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageJs(unittest.TestCase):
    def _node(self, body: str):
        src = _js_functions("trafficSeriesAllTime", "clonesSeriesAllTime",
                            "viewsSeriesAllTime", "isoDaysAgo", "rowOnOrBefore",
                            "pointOn", "sliceByPeriod", "cumAt") + "\n" + body
        out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_row_top_level_count_is_never_filed_under_its_own_date(self):
        # Sep 29's row carried Sep 23's 93 clones as its "today"; the old
        # union invented Sep 24-29 from it.
        res = self._node("""
          const rows = [
            {date:'2026-09-28', clones:{count:93,uniques:86}, clones_window:[{date:'2026-09-23',count:93,uniques:86}]},
            {date:'2026-09-29', clones:{count:93,uniques:86}, clones_window:[{date:'2026-09-22',count:99,uniques:85},{date:'2026-09-23',count:93,uniques:86}]},
            {date:'2026-10-14', clones:{count:90,uniques:83}, clones_window:[{date:'2026-09-29',count:140,uniques:111},{date:'2026-10-12',count:90,uniques:83}]},
          ];
          console.log(JSON.stringify(clonesSeriesAllTime(rows).map(p => [p.date, p.count])));
        """)
        self.assertEqual(res, [["2026-09-22", 99], ["2026-09-23", 93],
                               ["2026-09-29", 140], ["2026-10-12", 90]])

    def test_recovered_traffic_fills_only_days_no_window_has(self):
        res = self._node("""
          const rows = [
            {date:'2026-05-22', clones_window:[{date:'2026-05-08',count:5,uniques:4}],
             recovered_traffic:{clones:[{date:'2026-05-07',count:9,uniques:8},{date:'2026-05-08',count:999,uniques:1}]}},
          ];
          console.log(JSON.stringify(clonesSeriesAllTime(rows).map(p => [p.date, p.count])));
        """)
        self.assertEqual(res, [["2026-05-07", 9], ["2026-05-08", 5]])

    def test_deltas_look_up_by_date_across_gaps(self):
        res = self._node("""
          const rows = [{date:'2026-09-20',stars:229},{date:'2026-09-29',stars:240},{date:'2026-10-14',stars:256}];
          console.log(JSON.stringify([
            rowOnOrBefore(rows, isoDaysAgo(1, '2026-10-14')).stars,
            rowOnOrBefore(rows, isoDaysAgo(7, '2026-10-14')).stars,
            rowOnOrBefore(rows, '2026-09-01')]));
        """)
        self.assertEqual(res, [240, 240, None])

    def test_star_deltas_read_githubs_own_dates_across_an_archive_gap(self):
        # The snapshot before Oct 14 was Sep 29 (240): d/d read +16. GitHub's
        # star timeline says 254 on Oct 13 and 254 on Oct 7.
        res = self._node("""
          const tl = [{date:'2026-09-29',count:240},{date:'2026-10-06',count:254},{date:'2026-10-14',count:256}];
          console.log(JSON.stringify([cumAt(tl,'2026-10-13'), cumAt(tl,'2026-10-07'), cumAt(tl,'2026-01-01'), cumAt([], 'x')]));
        """)
        self.assertEqual(res, [254, 254, 0, None])


if __name__ == "__main__":
    unittest.main()
