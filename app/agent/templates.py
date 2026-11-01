"""Deterministic replies. Used when the LLM is off or fails the guardrail twice.

Each builder takes tool outputs only, and returns text in the requested language,
in the order: what changed, what to do, by when, why, source.
"""
from __future__ import annotations

from datetime import date, datetime

from ..i18n import fmt_day, fmt_time, num

CROP_NAMES = {"paddy_aman": {"bn": "আমন ধান", "en": "aman paddy"}, "potato": {"bn": "আলু", "en": "potato"}}
STAGE_NAMES = {
    "growing": {"bn": "ফসল বাড়ছে", "en": "still growing"},
    "ready": {"bn": "ধান পেকে গেছে, কাটার অপেক্ষায়", "en": "ripe and ready to cut"},
    "cut": {"bn": "ধান কাটা হয়ে গেছে", "en": "already cut"},
    "sold": {"bn": "বিক্রি হয়ে গেছে", "en": "sold"},
    "to_sow": {"bn": "এখনও বোনা হয়নি", "en": "not yet sown"},
}

ASK = {
    "village": {"bn": "আপনার গ্রামের নাম আর জেলা বলুন।", "en": "Which village and district is your field in?"},
    "village_retry": {"bn": "গ্রামটা মানচিত্রে খুঁজে পেলাম না। আপনার ব্লক বা কাছের বাজার শহরের নাম বলুন।",
                      "en": "I couldn't find that village on the map. Tell me your block or the nearest market town."},
    "crop": {"bn": "কোন ফসল? আমন ধান না আলু?", "en": "Which crop: aman paddy or potato?"},
    "stage": {"bn": "ধান কি পেকে গেছে, না কাটা হয়ে গেছে, না এখনও বাড়ছে?",
              "en": "Is the paddy ripe, already cut, or still growing?"},
    "area_bigha": {"bn": "জমি কত বিঘা?", "en": "How many bigha is the field?"},
    "cut_date": {"bn": "কবে ধান কেটেছেন? যেমন আজ, গতকাল, বা কত দিন আগে।",
                 "en": "When did you cut it? Today, yesterday, or how many days ago?"},
    "insured": {"bn": "আপনার কি বাংলা শস্য বিমা আছে? হ্যাঁ বা না বলুন।",
                "en": "Do you have Bangla Shasya Bima crop insurance? Yes or no."},
}

FIXED = {
    "greeting": {"bn": "নমস্কার। আমি ফসল সাথী। ধান কাটার নিরাপদ দিন, ভারী বৃষ্টির সতর্কতা আর বিমার দাবির সময়সীমা নিয়ে সাহায্য করতে পারি।",
                 "en": "Hello, I'm Fosol Sathi. I can help with safe days to cut paddy, heavy-rain warnings and insurance claim deadlines."},
    "out_of_scope": {"bn": "এ বিষয়ে আমি পরামর্শ দিতে পারি না। আপনার ব্লকের কৃষি প্রযুক্তি সহায়ক বা কিষাণ কল সেন্টারে জিজ্ঞেস করুন। আমি ধান কাটা, ভারী বৃষ্টি আর বিমার দাবিতে সাহায্য করি।",
                     "en": "I can't advise on that. Please ask your block's Krishi Prayukti Sahayak or the Kisan Call Centre. I help with paddy cutting, heavy rain and insurance claims."},
    "fertilizer_dose": {"bn": "আপনার মাটি পরীক্ষার কার্ড (সয়েল হেলথ কার্ড) ছাড়া সারের মাত্রা বলতে পারব না। ব্লক কৃষি অফিস থেকে মাটি পরীক্ষা করিয়ে নিন।",
                        "en": "I can't give a fertilizer dose without your Soil Health Card. Get your soil tested through the block agriculture office."},
    "stt_failed": {"bn": "ঠিক শুনতে পাইনি। আর একটু ধীরে বলুন, বা লিখে পাঠান।",
                   "en": "Couldn't hear that clearly. Say it again a little slower, or type it."},
    "forecast_unavailable": {"bn": "এখন আবহাওয়ার পূর্বাভাস পাওয়া যাচ্ছে না, আর ছয় ঘণ্টার মধ্যের কোনো তথ্যও নেই। তাই এখন কোনো পরামর্শ দিচ্ছি না। একটু পরে আবার জিজ্ঞেস করুন।",
                             "en": "The weather forecast is unavailable right now and there is no fetch from the last six hours, so I won't advise yet. Please ask again a little later."},
    "need_profile": {"bn": "আগে আপনার জমির কথা একটু জেনে নিই।", "en": "First, let me learn a little about your field."},
    "no_soil_card": {"bn": "আপনার মাটি পরীক্ষার কার্ডের তথ্য এখনও নেই। 'আমার জমি' পাতায় কার্ডের পিএইচ, জৈব কার্বন, নাইট্রোজেন, ফসফরাস আর পটাশ লিখুন, আমি বুঝিয়ে দেব।",
                     "en": "I don't have your Soil Health Card yet. Enter its pH, organic carbon, N, P and K on My farm and I'll explain them."},
    "market_unavailable": {"bn": "এখন মান্ডির দামের তথ্য পাওয়া যাচ্ছে না, তাই দাম নিয়ে কিছু বলছি না। কাছের বাজার বা সরকারি ধান ক্রয়কেন্দ্রে দর জেনে নিন।",
                           "en": "Mandi prices are unavailable right now, so I won't comment on prices. Check the rate at your nearest market or the government procurement centre."},
    "no_source": {"bn": "এ বিষয়ে আমার কাছে কোনো নির্ভরযোগ্য সূত্র নেই, তাই আন্দাজে বলছি না। ব্লক কৃষি অফিসে জিজ্ঞেস করুন।",
                  "en": "I don't have a reliable source for that, so I won't guess. Please ask your block agriculture office."},
    "potato_stage": {"bn": "আলু গাছ বাড়লে ঝলসা রোগের সতর্কতা দিই, আর বোনার আগে বোনার দিন বলি। আপনার আলু কোন অবস্থায় আছে?",
                     "en": "For potato I watch for late blight while it grows and suggest planting days before sowing. What stage is your potato at?"},
    "not_ready_stage": {"bn": "ধান পাকলে বা কাটা হলে কাটার নিরাপদ দিন বলব। ধান পাকলে আমাকে জানাবেন।",
                        "en": "I give safe cut days once the paddy is ripe or cut. Tell me when it's ripe."},
    "no_claim": {"bn": "এখন কোনো খোলা দাবি নেই। ধান কাটার পর ভারী বৃষ্টি হলে আমি দাবির চেকলিস্ট আর সময়সীমা খুলে দেব।",
                 "en": "There is no open claim. If heavy rain falls after cutting, I'll open the claim checklist and deadline."},
    "profile_saved": {"bn": "ঠিক আছে, সংশোধন করে নিলাম।", "en": "Got it, I've corrected that."},
    "confirm_thanks": {"bn": "ধন্যবাদ, আপনার জমির তথ্য রাখা হল।", "en": "Thank you, your farm details are saved."},
}


def fixed(key: str, lang: str) -> str:
    return FIXED[key]["en" if lang == "en" else "bn"]


def ask(field: str, lang: str) -> str:
    return ASK[field]["en" if lang == "en" else "bn"]


def readback(p: dict, lang: str) -> str:
    L = "en" if lang == "en" else "bn"
    parts = []
    if p.get("village"):
        parts.append((f"village {p['village']}" if L == "en" else f"গ্রাম {p['village']}"))
    if p.get("crop"):
        parts.append(CROP_NAMES[p["crop"]][L])
    if p.get("area_bigha") is not None:
        parts.append(f"{num(p['area_bigha'], L)} " + ("bigha" if L == "en" else "বিঘা"))
    if p.get("stage"):
        parts.append(STAGE_NAMES[p["stage"]][L])
    if p.get("cut_date"):
        parts.append(("cut on " if L == "en" else "কাটা হয়েছে ") + fmt_day(p["cut_date"], L))
    if p.get("insured") is not None:
        parts.append(("insured" if p["insured"] else "not insured") if L == "en"
                     else ("বিমা আছে" if p["insured"] else "বিমা নেই"))
    if L == "en":
        return "Your farm: " + ", ".join(parts) + ". Is that right? If not, tell me the one thing that's wrong."
    return "আপনার জমি: " + ", ".join(parts) + "। ঠিক আছে? না হলে যেটা ভুল সেটা বলুন।"


def _src(facts: dict, lang: str) -> str:
    when = facts.get("forecast_fetched_at")
    cached = facts.get("forecast_cached")
    t = fmt_time(datetime.fromisoformat(when), lang) if when else ""
    if lang == "en":
        return f" Source: Open-Meteo forecast, {'earlier data from ' if cached else 'fetched '}{t}."
    return f" সূত্র: ওপেন-মিটিও পূর্বাভাস, {'আগের তথ্য, ' if cached else ''}{t}।"


def harvest(facts: dict, lang: str, with_source: bool = True) -> str:
    L = "en" if lang == "en" else "bn"
    safe = facts.get("safe_cut_days") or []
    rain = facts.get("first_rain_day")
    if safe:
        d = fmt_day(safe[0], L)
        more = [fmt_day(x, L) for x in safe[1:]]
        if L == "en":
            s = f"The safe day to cut your paddy is {d}" + (f"; also {', '.join(more)}" if more else "") + "."
            s += " Cut then so it can dry in the field before rain."
        else:
            s = f"ধান কাটার নিরাপদ দিন {d}" + (f"; এছাড়া {', '.join(more)}" if more else "") + "।"
            s += " তখন কাটুন, যাতে বৃষ্টির আগে মাঠে শুকোতে পারে।"
    else:
        n = facts.get("horizon_days", 5)
        s = (f"There is no safe day to cut in the next {n} days. Wait; I'll tell you when that changes."
             if L == "en" else
             f"আগামী {num(n, L)} দিনে ধান কাটার নিরাপদ দিন নেই। অপেক্ষা করুন; বদলালে আমি জানাব।")
    if rain:
        s += (f" Rain is likely on {fmt_day(rain, L)}." if L == "en" else f" {fmt_day(rain, L)} বৃষ্টির সম্ভাবনা আছে।")
    return s + (_src(facts, L) if with_source else "")


def calamity(facts: dict, lang: str, with_source: bool = True) -> str:
    L = "en" if lang == "en" else "bn"
    a = facts.get("action")
    mm = facts.get("next24_mm")
    if a == "protect_now":
        s = (f"Heavy rain warning: about {num(mm, L)} mm in the next 24 hours. Move the paddy to a raised dry place or cover it now."
             if L == "en" else
             f"ভারী বৃষ্টির সতর্কতা: আগামী ২৪ ঘণ্টায় প্রায় {num(mm, L)} মিমি বৃষ্টি। এখনই ধান উঁচু শুকনো জায়গায় সরান বা ঢেকে দিন।")
    elif a == "cover_heap":
        s = (f"About {num(mm, L)} mm of rain is expected in the next 24 hours. Cover your cut paddy heap today."
             if L == "en" else
             f"আগামী ২৪ ঘণ্টায় প্রায় {num(mm, L)} মিমি বৃষ্টি হতে পারে। আজই কাটা ধানের গাদা ঢেকে দিন।")
    else:
        s = (f"No heavy rain expected in the next 24 hours (about {num(mm, L)} mm)."
             if L == "en" else
             f"আগামী ২৪ ঘণ্টায় ভারী বৃষ্টির আশঙ্কা নেই (প্রায় {num(mm, L)} মিমি)।")
    return s + (_src(facts, L) if with_source else "")


def blight(facts: dict, lang: str, with_source: bool = True) -> str:
    L = "en" if lang == "en" else "bn"
    a = facts.get("action")
    if a == "spray_now":
        s = ("Weather has favoured potato late blight on your field. Spray a protective fungicide now, as your block agriculture office advises."
             if L == "en" else "আপনার জমিতে আলুর ঝলসা রোগের অনুকূল আবহাওয়া চলছে। ব্লক কৃষি অফিসের পরামর্শমতো এখনই প্রতিরোধক ছত্রাকনাশক স্প্রে করুন।")
    elif a == "spray_by":
        s = (f"Late blight risk is forecast from {fmt_day(facts['first_risk_day'], L)}. Spray a protective fungicide by {fmt_day(facts['spray_by'], L)}."
             if L == "en" else
             f"{fmt_day(facts['first_risk_day'], L)} থেকে ঝলসা রোগের ঝুঁকি। {fmt_day(facts['spray_by'], L)}-এর মধ্যে প্রতিরোধক ছত্রাকনাশক স্প্রে করুন।")
    else:
        s = ("No late blight risk this week. No spray needed now." if L == "en"
             else "এই সপ্তাহে ঝলসা রোগের ঝুঁকি নেই। এখন স্প্রে দরকার নেই।")
    return s + (_src(facts, L) if with_source else "")


def sowing(facts: dict, lang: str, with_source: bool = True) -> str:
    L = "en" if lang == "en" else "bn"
    days = facts.get("plant_days") or []
    if days:
        s = (f"Good days to plant potato start {fmt_day(days[0], L)}." if L == "en"
             else f"আলু বোনার ভালো সময় {fmt_day(days[0], L)} থেকে।")
    else:
        n = facts.get("horizon_days", 7)
        s = (f"Not yet: no good planting day in the next {n} days. Nights are still too warm. I'll tell you when that changes."
             if L == "en" else f"এখনও নয়: আগামী {num(n, L)} দিনে আলু বোনার ভালো দিন নেই, রাত এখনও গরম। বদলালে জানাব।")
    return s + (_src(facts, L) if with_source else "")


def soil(facts: dict, lang: str) -> str:
    L = "en" if lang == "en" else "bn"
    return (facts.get("reason_en") if L == "en" else facts.get("reason_bn")) or ""


def market(facts: dict, lang: str) -> str:
    L = "en" if lang == "en" else "bn"
    text = (facts.get("reason_en") if L == "en" else facts.get("reason_bn")) or ""
    when = facts.get("forecast_fetched_at")
    t = fmt_time(datetime.fromisoformat(when), L) if when else ""
    return text + (f" Source: Agmarknet via data.gov.in, fetched {t}." if L == "en"
                   else f" সূত্র: অ্যাগমার্কনেট (data.gov.in), {t}।")


def knowledge(facts: dict, lang: str) -> str:
    """The passage verbatim, with its source. Never paraphrased."""
    L = "en" if lang == "en" else "bn"
    p = facts["passages"][0]
    if L == "en":
        return f"From {p['source']}: \"{p['text']}\""
    return f"{p['source']}-এ লেখা আছে (ইংরেজিতে): \"{p['text']}\""


def claim(facts: dict, lang: str) -> str:
    L = "en" if lang == "en" else "bn"
    if not facts.get("open"):
        return fixed("no_claim", L)
    dl = datetime.fromisoformat(facts["deadline_at"])
    hrs = facts.get("hours_left")
    if facts.get("closed"):
        return ("The 72-hour window has closed. Contact your insurer and the block agriculture office."
                if L == "en" else "৭২ ঘণ্টার সময়সীমা পেরিয়ে গেছে। আপনার বিমা কোম্পানি আর ব্লক কৃষি অফিসে যোগাযোগ করুন।")
    if L == "en":
        return (f"Report your rain loss by {fmt_time(dl, 'en')}, about {hrs} hours from now. Take photos of the wet paddy, "
                f"copy the report message on the claim screen, and send it to your insurer. "
                f"Source: Bangla Shasya Bima prospectus, report within {facts.get('report_within_hours')} hours.")
    return (f"বৃষ্টিতে ক্ষতির কথা {fmt_time(dl, 'bn')}-এর মধ্যে জানান, হাতে প্রায় {num(hrs, 'bn')} ঘণ্টা আছে। ভেজা ধানের ছবি তুলুন, "
            f"দাবির পাতায় ক্ষতির রিপোর্টের বার্তা কপি করে বিমা কোম্পানিকে পাঠান। "
            f"সূত্র: বাংলা শস্য বিমার নিয়ম, {num(facts.get('report_within_hours'), 'bn')} ঘণ্টার মধ্যে জানাতে হয়।")


def explain(facts: dict, lang: str) -> str:
    L = "en" if lang == "en" else "bn"
    reason = facts.get("reason_en") if L == "en" else (facts.get("reason_bn") or facts.get("reason_en"))
    return (reason or "") + _src(facts, L)


def report_message(farm: dict, event_at: datetime, rain_mm: float, lang: str) -> str:
    L = "en" if lang == "en" else "bn"
    crop = CROP_NAMES.get(farm.get("crop") or "paddy_aman")[L]
    cut = fmt_day(farm["cut_date"], L) if farm.get("cut_date") else ("unknown" if L == "en" else "অজানা")
    area = num(farm["area_bigha"], L) if farm.get("area_bigha") is not None else ("unknown" if L == "en" else "অজানা")
    who = farm.get("name") or ("Farmer" if L == "en" else "কৃষক")
    if L == "en":
        return (f"To the insurer / Block Agriculture Office: I, {who}, of village {farm.get('village')}, "
                f"{farm.get('district') or ''}, report post-harvest loss of {crop} under Bangla Shasya Bima. "
                f"Area: {area} bigha. Cut on: {cut}. Heavy rain from {fmt_time(event_at, 'en')} "
                f"(estimated {num(rain_mm, 'en')} mm in 24 hours, Open-Meteo model estimate) wet the cut crop lying in the field. "
                f"Photos attached. Policy/receipt number: ______. Mobile: ______.")
    return (f"বিমা কোম্পানি / ব্লক কৃষি অফিস সমীপেষু: আমি {who}, গ্রাম {farm.get('village')}, {farm.get('district') or ''}। "
            f"বাংলা শস্য বিমার অধীনে {crop}-এর ফসল কাটার পরের ক্ষতি জানাচ্ছি। জমি: {area} বিঘা। কাটা হয়েছে: {cut}। "
            f"{fmt_time(event_at, 'bn')} থেকে ভারী বৃষ্টিতে (২৪ ঘণ্টায় আনুমানিক {num(rain_mm, 'bn')} মিমি, ওপেন-মিটিও মডেলের হিসাব) "
            f"মাঠে পড়ে থাকা কাটা ফসল ভিজে গেছে। ছবি সংযুক্ত। পলিসি/রসিদ নম্বর: ______। মোবাইল: ______।")


CHECKLIST = [
    {"key": "photos", "bn": "ভেজা বা ক্ষতিগ্রস্ত ধানের পরিষ্কার ছবি তুলুন (৩–৪টি)", "en": "Take 3–4 clear photos of the wet or damaged paddy"},
    {"key": "receipt", "bn": "বাংলা শস্য বিমার রসিদ বা পলিসি নম্বর হাতের কাছে রাখুন", "en": "Keep your Bangla Shasya Bima receipt or policy number ready"},
    {"key": "copied", "bn": "ক্ষতির রিপোর্টের বার্তা কপি করুন", "en": "Copy the loss report message"},
    {"key": "contacted", "bn": "বার্তা আর ছবি বিমা কোম্পানি বা ব্লক কৃষি অফিসে (কৃষি প্রযুক্তি সহায়ক) পাঠান", "en": "Send the message and photos to your insurer or the block agriculture office (Krishi Prayukti Sahayak)"},
]


def hours_between(a: datetime, b: datetime) -> int:
    return max(0, int((b - a).total_seconds() // 3600))


def iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None
