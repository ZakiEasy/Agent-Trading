import sys
from src.institutional_engine import generate_8_step_protocol_analysis
import pprint

res = generate_8_step_protocol_analysis("NVDA", 10000)
pprint.pprint(res)
