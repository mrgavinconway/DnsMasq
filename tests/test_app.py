import tempfile
import unittest
from pathlib import Path

import app


class ConfigTests(unittest.TestCase):
    def test_reservations(self):
        text = app.render_reservations([{"hostname": "printer", "ip": "192.168.1.20", "mac": "AA:BB:CC:DD:EE:FF"}])
        self.assertIn("dhcp-host=aa:bb:cc:dd:ee:ff,192.168.1.20,printer", text)

    def test_duplicate_ip_rejected(self):
        with self.assertRaises(ValueError):
            app.render_reservations([
                {"hostname": "one", "ip": "10.0.0.2", "mac": "00:00:00:00:00:01"},
                {"hostname": "two", "ip": "10.0.0.2", "mac": "00:00:00:00:00:02"},
            ])

    def test_dns(self):
        self.assertIn("address=/nas.home/10.0.0.3", app.render_dns([{"hostname": "nas.home", "ip": "10.0.0.3"}]))

    def test_read_dns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dns.conf"
            path.write_text("address=/nas.home/10.0.0.3\n")
            self.assertEqual(app.read_managed(path, "address=/"), [{"hostname": "nas.home", "ip": "10.0.0.3"}])

    def test_leases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "leases"
            path.write_text("4102444800 aa:bb:cc:dd:ee:ff 10.0.0.7 laptop 01:aa\n")
            rows = app.read_leases(path)
            self.assertEqual(rows[0]["hostname"], "laptop")


if __name__ == "__main__":
    unittest.main()
