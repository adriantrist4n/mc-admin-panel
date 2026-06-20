#!/usr/bin/env python3
"""Tests de las funciones puras de dashboard.py: parsers de respuestas RCON/
jcmd/jstat y helpers de formato. No arrancan la UI ni el hilo Monitor."""
import os
import sys
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
import dashboard


class TestParseHeapInfo(unittest.TestCase):
    def test_zgc_format(self):
        text = "241064:\nZHeap            used 1022M, capacity 2048M, max capacity 8192M\n"
        self.assertEqual(dashboard.parse_heap_info(text), (1022.0, 2048.0, 8192.0))

    def test_g1_generic_format(self):
        text = "garbage-first heap   total 2097152K, used 1048576K [...]"
        used, cap, mx = dashboard.parse_heap_info(text)
        self.assertEqual(used, 1024.0)
        self.assertEqual(cap, 2048.0)
        self.assertEqual(mx, 2048.0)

    def test_none_on_garbage_or_empty(self):
        self.assertIsNone(dashboard.parse_heap_info("nada que ver aquí"))
        self.assertIsNone(dashboard.parse_heap_info(None))
        self.assertIsNone(dashboard.parse_heap_info(""))


class TestParseJstat(unittest.TestCase):
    def test_valid_output(self):
        text = (
            "    S0C    S1C    S0U    S1U      EC       EU        OC         OU"
            "        MC     MU    CCSC   CCSU   YGC     YGCT    FGC    FGCT    CGC    CGCT       GCT   \n"
            "       -      -      -      -    1085440,0      26624,0    1011712,0    1011712,0"
            "   152832,0   151501,1   25216,0   24619,2    270     0,002     -         -  3020     0,033     0,036\n"
        )
        gc = dashboard.parse_jstat(text)
        self.assertEqual(gc["YGC"], 270)
        self.assertEqual(gc["CGC"], 3020)
        self.assertIsNone(gc["FGC"])
        self.assertAlmostEqual(gc["GCT"], 0.036)

    def test_none_on_too_few_lines(self):
        self.assertIsNone(dashboard.parse_jstat("solo una linea"))
        self.assertIsNone(dashboard.parse_jstat(None))


class TestParseTick(unittest.TestCase):
    def test_full_response(self):
        text = (
            "The game is running normallyTarget tick rate: 20.0 per second."
            "Average time per tick: 0.1ms (Target: 50.0ms)Percentiles: "
            "P50: 0.1ms P95: 0.2ms P99: 0.2ms. Sample: 100"
        )
        tick = dashboard.parse_tick(text)
        self.assertEqual(tick["target"], 20.0)
        self.assertEqual(tick["mspt"], 0.1)
        self.assertEqual(tick["p50"], 0.1)
        self.assertEqual(tick["p95"], 0.2)
        self.assertEqual(tick["p99"], 0.2)
        self.assertEqual(tick["tps"], 20.0)  # clamped al target

    def test_high_mspt_lowers_tps(self):
        text = "Target tick rate: 20.0 per second. Average time per tick: 100.0ms"
        tick = dashboard.parse_tick(text)
        self.assertEqual(tick["tps"], 10.0)  # 1000 / 100

    def test_none_input(self):
        self.assertIsNone(dashboard.parse_tick(None))
        self.assertIsNone(dashboard.parse_tick(""))


class TestParseList(unittest.TestCase):
    def test_empty_server(self):
        d = dashboard.parse_list("There are 0 of a max of 8 players online: ")
        self.assertEqual(d, {"online": 0, "max": 8, "names": []})

    def test_with_players(self):
        d = dashboard.parse_list("There are 2 of a max of 8 players online: Adrian, Lucia")
        self.assertEqual(d["online"], 2)
        self.assertEqual(d["names"], ["Adrian", "Lucia"])

    def test_unparseable_or_none(self):
        self.assertIsNone(dashboard.parse_list("respuesta rara"))
        self.assertIsNone(dashboard.parse_list(None))


class TestWorldParsers(unittest.TestCase):
    def test_difficulty(self):
        self.assertEqual(dashboard.parse_difficulty("The difficulty is Peaceful"), "Peaceful")
        self.assertIsNone(dashboard.parse_difficulty("otra cosa"))

    def test_gametime(self):
        self.assertEqual(dashboard.parse_gametime("The game time is 552298 tick(s)"), 552298)

    def test_daytime(self):
        self.assertEqual(dashboard.parse_daytime("Timeline minecraft:day is at 1251 tick(s)"), 1251)

    def test_forceload_none_loaded(self):
        text = "No force loaded chunks were found in minecraft:overworld"
        self.assertEqual(dashboard.parse_forceload(text), 0)

    def test_forceload_with_chunks(self):
        text = "Force loaded chunks in minecraft:overworld: [1, 2], [3, 4], [-1, -2]"
        self.assertEqual(dashboard.parse_forceload(text), 3)

    def test_worldborder(self):
        text = "The world border is currently 59999968 block(s) wide"
        self.assertEqual(dashboard.parse_worldborder(text), 59999968.0)


class TestTicksToClock(unittest.TestCase):
    def test_start_of_day(self):
        clock, is_day = dashboard.ticks_to_clock(0)
        self.assertEqual(clock, "06:00")
        self.assertTrue(is_day)

    def test_night(self):
        clock, is_day = dashboard.ticks_to_clock(13000)
        self.assertEqual(clock, "19:00")
        self.assertFalse(is_day)

    def test_none_input(self):
        clock, _ = dashboard.ticks_to_clock(None)
        self.assertEqual(clock, "—")


class TestEntityParsers(unittest.TestCase):
    def test_entity_scalar(self):
        self.assertEqual(
            dashboard.entity_scalar("Alice has the following entity data: 20.0f"), 20.0
        )
        self.assertIsNone(dashboard.entity_scalar(None))

    def test_entity_str(self):
        text = 'Alice has the following entity data: "minecraft:overworld"'
        self.assertEqual(dashboard.entity_str(text), "overworld")

    def test_entity_pos(self):
        text = "Alice has the following entity data: [123.4d, 64.0d, -45.6d]"
        self.assertEqual(dashboard.entity_pos(text), "(123, 64, -46)")
        self.assertIsNone(dashboard.entity_pos(None))


class TestFormatHelpers(unittest.TestCase):
    def test_human_bytes(self):
        self.assertEqual(dashboard.human(None), "—")
        self.assertEqual(dashboard.human(512), "512B")
        self.assertEqual(dashboard.human(2048), "2K")
        self.assertEqual(dashboard.human(3_500_000_000), "3.3G")

    def test_human_per_sec_suffix(self):
        self.assertTrue(dashboard.human(1024, per_sec=True).endswith("/s"))

    def test_color_for_thresholds(self):
        self.assertEqual(dashboard.color_for(10), "green")
        self.assertEqual(dashboard.color_for(70), "yellow")
        self.assertEqual(dashboard.color_for(90), "red")

    def test_color_for_reverse(self):
        self.assertEqual(dashboard.color_for(96, 80, 95, reverse=True), "green")
        self.assertEqual(dashboard.color_for(50, 80, 95, reverse=True), "red")

    def test_fmt_uptime(self):
        self.assertEqual(dashboard.fmt_uptime(59), "0m")
        self.assertEqual(dashboard.fmt_uptime(3661), "1h 1m")
        self.assertEqual(dashboard.fmt_uptime(90000), "1d 1h 0m")

    def test_fmt_ms(self):
        self.assertEqual(dashboard.fmt_ms(None), "—")
        self.assertEqual(dashboard.fmt_ms(1.23), "1.2")

    def test_temp_color(self):
        self.assertEqual(dashboard.temp_color(None), "white")
        self.assertEqual(dashboard.temp_color(40), "green")
        self.assertEqual(dashboard.temp_color(70), "yellow")
        self.assertEqual(dashboard.temp_color(90), "red")

    def test_mspt_color(self):
        self.assertEqual(dashboard.mspt_color(None), "white")
        self.assertEqual(dashboard.mspt_color(10), "green")
        self.assertEqual(dashboard.mspt_color(40), "yellow")
        self.assertEqual(dashboard.mspt_color(60), "red")

    def test_fmt_gc(self):
        self.assertEqual(dashboard.fmt_gc(None), "—")
        gc = {"YGC": 270, "CGC": 3020, "FGC": 0, "GCT": 0.036}
        s = dashboard.fmt_gc(gc)
        self.assertIn("270y", s)
        self.assertIn("3020c", s)
        self.assertNotIn("0f", s)  # FGC=0 no debe aparecer
        self.assertIn("36ms", s)


class TestGenericIdentity(unittest.TestCase):
    """Confirma que el dashboard ya no depende de esta instalación concreta."""

    def test_world_dir_uses_mcconfig(self):
        import mcconfig
        self.assertEqual(dashboard.WORLD_DIR, mcconfig.world_dir(dashboard.SERVER_DIR))

    def test_no_hardcoded_instance_name(self):
        meta = dashboard.server_meta()
        self.assertIn("name", meta)
        self.assertNotEqual(meta["name"], "")


if __name__ == "__main__":
    unittest.main()
