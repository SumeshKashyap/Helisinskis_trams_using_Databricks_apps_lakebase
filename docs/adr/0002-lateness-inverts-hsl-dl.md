# Lateness is the negation of HSL's `dl`

HSL's `dl` field is negative when a vehicle runs behind schedule, the opposite of most people's intuition. We convert it once, in silver, to **Lateness** (`lateness_s = -dl`, positive = late) and never expose `dl` beyond bronze. This is a deliberate deviation from HSL's documentation: do not "fix" it back to match the source.
