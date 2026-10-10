"""Unit converter and calculator (add-on).  "convert 5 km to miles" / "10 kg kitne pounds" / "calculate 12*(3+4)" / "20 percent of 450".
Pure offline maths. The calculator parses with `ast` and allows numbers and + - * / ** % only - nothing is ever eval()'d."""
import ast
import operator
import re

from ._rules import hook

LINEAR = {"length": {"mm": 0.001, "cm": 0.01, "m": 1, "km": 1000, "inch": 0.0254, "ft": 0.3048, "yard": 0.9144, "mile": 1609.344},
          "mass": {"g": 0.001, "kg": 1, "pound": 0.45359237, "ounce": 0.028349523, "quintal": 100, "ton": 1000},
          "volume": {"ml": 0.001, "l": 1, "gallon": 3.785411784, "cup": 0.2365882365},
          "speed": {"mps": 1, "kmph": 1 / 3.6, "mph": 0.44704},
          "data": {"kb": 1, "mb": 1024, "gb": 1024 ** 2, "tb": 1024 ** 3}}
ALIAS = {"millimeter": "mm", "millimeters": "mm", "centimeter": "cm", "centimeters": "cm", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
         "kilometer": "km", "kilometers": "km", "kilometre": "km", "kilometres": "km", "inches": "inch", "in": "inch", "foot": "ft", "feet": "ft",
         "yards": "yard", "miles": "mile", "gram": "g", "grams": "g", "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg", "pounds": "pound",
         "lb": "pound", "lbs": "pound", "ounces": "ounce", "oz": "ounce", "tons": "ton", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
         "gallons": "gallon", "cups": "cup", "km/h": "kmph", "kph": "kmph", "m/s": "mps"}
TEMP = {"c", "f", "k", "celsius", "fahrenheit", "kelvin"}
CONVERT_RX = re.compile(r"^(?:jarvis[,\s]+)?(?:please\s+)?(?:convert\s+)?(-?\d+(?:\.\d+)?)\s*°?\s*([a-z/]+)\s+(?:to|in|into|kitne|ko|se)\s+([a-z/]+)(?:\s+(?:hota|hote|hai|hain|me|mein|banta|batao))*$", re.I)
PERCENT_RX = re.compile(r"^(?:jarvis[,\s]+)?(?:what is\s+|kya hai\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent|prati?shat)\s+of\s+(\d+(?:\.\d+)?)$", re.I)
CALC_RX = re.compile(r"^(?:jarvis[,\s]+)?(?:calculate|calc|compute|hisab|solve)\s*[:,]?\s*(.+)$", re.I)
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}


def _canon(u):
    u = u.lower()
    return ALIAS.get(u, u)


def safe_eval(expr):
    expr = expr.lower().replace("x", "*").replace("×", "*").replace("÷", "/").replace("plus", "+").replace("minus", "-").replace("into", "*")
    expr = re.sub(r"\bdivided by\b", "/", expr)
    expr = re.sub(r"\btimes\b|\bmultiplied by\b", "*", expr).replace("^", "**").rstrip("?= .")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 100:
                raise ValueError("exponent too large")
            return OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        raise ValueError("unsupported")
    return ev(ast.parse(expr, mode="eval"))


def _num(x):
    x = round(float(x), 4)
    return str(int(x)) if x.is_integer() else f"{x:g}"


def convert(value, src, dst):
    s, d = _canon(src), _canon(dst)
    if s[:1] in "cfk" and d[:1] in "cfk" and (s in TEMP or len(s) == 1) and (d in TEMP or len(d) == 1):
        s, d = s[0], d[0]
        c = value if s == "c" else (value - 32) * 5 / 9 if s == "f" else value - 273.15
        return c if d == "c" else c * 9 / 5 + 32 if d == "f" else c + 273.15
    for table in LINEAR.values():
        if s in table and d in table:
            return value * table[s] / table[d]
    return None


def handle(cmd):
    c = cmd.strip().rstrip("?.! ")
    m = PERCENT_RX.match(c)
    if m:
        return f"{_num(float(m.group(1)) * float(m.group(2)) / 100)}, Boss."
    m = CONVERT_RX.match(c)
    if m:
        out = convert(float(m.group(1)), m.group(2), m.group(3))
        if out is not None:
            return f"{_num(m.group(1))} {m.group(2)} is {_num(out)} {m.group(3)}, Boss."
        return None
    m = CALC_RX.match(c)
    if m:
        try:
            return f"The answer is {_num(safe_eval(m.group(1)))}, Boss."
        except ZeroDivisionError:
            return "That divides by zero, Boss."
        except (ValueError, SyntaxError, OverflowError):
            return None
    return None


def install(host):
    return hook(host, "_converter_addon", handle, "converter")
