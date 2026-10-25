import unittest

from eaj.money import MoneyError, apply_bp, fmt_bp_input, fmt_brl, parse_money, parse_percent_bp, split_by_shares


class MoneyTests(unittest.TestCase):
    def test_parse_formats(self):
        self.assertEqual(parse_money('30'), 3000)
        self.assertEqual(parse_money('30,00'), 3000)
        self.assertEqual(parse_money('1.234,56'), 123456)
        self.assertEqual(parse_money('R$ 0,05'), 5)
        self.assertEqual(parse_money('10.5'), 1050)
        self.assertEqual(parse_money('0,005'), 1)            # arredonda meio centavo para cima

    def test_parse_rejects(self):
        for bad in ('', 'abc', '-1', 'nan', 'inf'):
            with self.assertRaises(MoneyError, msg=bad):
                parse_money(bad)
        with self.assertRaises(MoneyError):
            parse_money('0', allow_zero=False)

    def test_format(self):
        self.assertEqual(fmt_brl(2178670), 'R$ 21.786,70')
        self.assertEqual(fmt_brl(-500), '−R$ 5,00')
        self.assertEqual(fmt_brl(None), '—')

    def test_percent(self):
        self.assertEqual(parse_percent_bp('50'), 5000)
        self.assertEqual(parse_percent_bp('12,5%'), 1250)
        self.assertEqual(fmt_bp_input(1250), '12,5')
        self.assertEqual(fmt_bp_input(5000), '50')
        with self.assertRaises(MoneyError):
            parse_percent_bp('101')
        self.assertEqual(apply_bp(10001, 5000), 5001)

    def test_split_never_loses_cents(self):
        for total in (1, 3, 100, 10001, 999999):
            parts = split_by_shares(total, [5000, 5000])
            self.assertEqual(sum(parts), total)
            self.assertLessEqual(abs(parts[0] - parts[1]), 1)
        self.assertEqual(sum(split_by_shares(100, [3334, 3333, 3333])), 100)


if __name__ == '__main__':
    unittest.main()
