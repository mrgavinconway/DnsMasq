import tempfile
import unittest
from pathlib import Path

import app


class ConfigTests(unittest.TestCase):
    def test_reservations(self):
        text = app.render_reservations([{"hostname": "printer", "ip": "192.168.1.20", "mac": "AA:BB:CC:DD:EE:FF"}])
        self.assertIn("aa:bb:cc:dd:ee:ff,printer,192.168.1.20", text)

    def test_duplicate_ip_rejected(self):
        with self.assertRaises(ValueError):
            app.render_reservations([
                {"hostname": "one", "ip": "10.0.0.2", "mac": "00:00:00:00:00:01"},
                {"hostname": "two", "ip": "10.0.0.2", "mac": "00:00:00:00:00:02"},
            ])

    def test_dns(self):
        self.assertIn("10.0.0.3\tnas.home", app.render_dns([{"hostname": "nas.home", "ip": "10.0.0.3"}]))

    def test_read_dns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dns.conf"
            path.write_text("10.0.0.3 nas.home\n")
            self.assertEqual(app.read_dns(path), [{"hostname": "nas.home", "ip": "10.0.0.3"}])

    def test_read_reservations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reservations"
            path.write_text("aa:bb:cc:dd:ee:ff,printer,192.168.1.20\n")
            self.assertEqual(app.read_reservations(path), [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.20", "hostname": "printer"}])

    def test_leases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "leases"
            path.write_text("4102444800 aa:bb:cc:dd:ee:ff 10.0.0.7 laptop 01:aa\n")
            rows = app.read_leases(path)
            self.assertEqual(rows[0]["hostname"], "laptop")

    def test_system_stats_shape(self):
        stats = app.system_stats()
        self.assertIn("temperature", stats)
        self.assertIn("memoryPercent", stats)
        self.assertIn("diskPercent", stats)
        self.assertGreaterEqual(stats["cpuCount"], 1)

    def test_read_number_missing(self):
        self.assertIsNone(app.read_number(Path("/definitely/not/a/real/file")))

    def test_read_number_uses_first_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "uptime"
            path.write_text("12345.67 9876.54\n")
            self.assertEqual(app.read_number(path), 12345.7)

    def test_private_mac_vendor(self):
        result = app.mac_vendor("02:00:00:00:00:01")
        self.assertTrue(result["private"])
        self.assertIn("randomized", result["vendor"])

    def test_invalid_mac_vendor(self):
        with self.assertRaises(ValueError):
            app.mac_vendor("not-a-mac")

    def test_registered_mac_vendor(self):
        original = app.OUI_CACHE
        try:
            app.OUI_CACHE = {"A8BBCC": "Example Devices Ltd"}
            result = app.mac_vendor("a8:bb:cc:00:00:01")
            self.assertEqual(result["vendor"], "Example Devices Ltd")
            self.assertFalse(result["private"])
        finally:
            app.OUI_CACHE = original

    def test_leases_include_vendor(self):
        original = app.OUI_CACHE
        try:
            app.OUI_CACHE = {"A8BBCC": "Example Devices Ltd"}
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "leases"
                path.write_text("4102444800 a8:bb:cc:00:00:01 10.0.0.7 laptop 01:aa\n")
                self.assertEqual(app.leases_with_vendors(path)[0]["vendor"], "Example Devices Ltd")
        finally:
            app.OUI_CACHE = original


if __name__ == "__main__":
    unittest.main()
