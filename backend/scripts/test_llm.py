from app.core.config import settings
from app.core.llm import get_llm

print("Providers in order:", settings.available_llms())

llm = get_llm()

print("English:", llm.chat([
    {"role": "system", "content": "You reply in one short sentence."},
    {"role": "user", "content": "Say hello and name a river in Nepal."},
], max_tokens=60))
print("  answered by:", llm.last_provider)

print("Nepali:", llm.chat([
    {"role": "system", "content": "Translate to Nepali. Keep all numbers exactly as written."},
    {"role": "user", "content": "12 villages are cut off and 3 bridges are damaged."},
], max_tokens=120))
print("  answered by:", llm.last_provider)