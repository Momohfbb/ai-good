"""Example plugin: unit conversion that works offline.

Copy this file to start a new plugin. Turn it on in Settings → Plugins.
"""

PLUGIN = {"name": "unit_converter", "version": "1.0", "api": 1,
          "description": "تحويل الوحدات (كلم/ميل، كغ/رطل، مئوية/فهرنهايت) بدون إنترنت"}

FACTORS = {("km", "mi"): 0.621371, ("mi", "km"): 1.609344, ("kg", "lb"): 2.204623, ("lb", "kg"): 0.453592,
           ("m", "ft"): 3.28084, ("ft", "m"): 0.3048}


def register(api):
    async def convert(ctx, value: str, from_unit: str, to_unit: str):
        v = float(str(value).replace(",", "."))
        f, t = from_unit.lower().strip(), to_unit.lower().strip()
        if (f, t) == ("c", "f"):
            out = v * 9 / 5 + 32
        elif (f, t) == ("f", "c"):
            out = (v - 32) * 5 / 9
        elif (f, t) in FACTORS:
            out = v * FACTORS[(f, t)]
        else:
            raise api.ActionError("unsupported_units")
        return api.StepResult(f"{v:g} {f} = {out:.4g} {t}")

    api.add_action("manager", "convert_units", "Convert units (km/mi, kg/lb, m/ft, c/f)",
                   {"value": "number", "from_unit": "km|mi|kg|lb|m|ft|c|f", "to_unit": "km|mi|kg|lb|m|ft|c|f"},
                   convert)
