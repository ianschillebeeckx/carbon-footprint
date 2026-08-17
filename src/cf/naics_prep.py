"""Build the NAICS retrieval index for v2 transaction classification.

Reads the EPA supply-chain emission factors CSV (v1.3.0, kg CO2e per
2022 USD, purchaser price) and produces data/naics_index.json: one entry
per 2022 NAICS-6 code with its factors, display category (CoolClimate's
Goods & Services taxonomy + Digital Subscriptions, or "excluded"), and a
search_text used for embedding retrieval. Consumer-relevant codes get
hand-written keyword/example enrichment so short merchant strings retrieve
well.

The factors' native key is the 2017 NAICS code (unique per row); the 2022
code is a crosswalk and NOT unique — 79 codes span multiple 2017 rows.
Entries stay keyed on 2022 codes (the vocabulary the LLM and vector tiers
speak) but each duplicated 2022 code resolves to ONE deliberately chosen
2017 row (DUP_PICK, else the first/physical-store row), and every entry
records its resolved 2017 code + Reference USEEIO Code for provenance.
"""

import csv
import json
from pathlib import Path

FACTORS_CSV = Path("data/naics2022_with_2017_emission_factors_1.csv")
INDEX_FILE = Path("data/naics_index.json")

# Display categories. G&S categories map to CoolClimate calculator fields;
# "digital_subscriptions" is ours (feeds Information & Communication in the
# calculator); "excluded" = food/travel/utilities/housing/income (out of
# scope for v2's Goods & Services focus, but still classified so they're
# excluded knowingly rather than silently).
CATEGORIES = {
    "goods_furniture_appliances": ("Goods", "Furniture & Appliances"),
    "goods_clothing": ("Goods", "Clothing"),
    "goods_entertainment": ("Goods", "Entertainment"),
    "goods_paper_office_reading": ("Goods", "Paper, Office, & Reading"),
    "goods_personal_care_cleaning": ("Goods", "Personal Care & Cleaning"),
    "goods_auto_parts": ("Goods", "Auto Parts"),
    "goods_medical": ("Goods", "Medical"),
    "goods_other": ("Goods", "Other Goods"),
    "services_health_care": ("Services", "Health Care"),
    "services_education": ("Services", "Education"),
    "services_information_communication": ("Services", "Information & Communication"),
    "digital_subscriptions": ("Services", "Digital Subscriptions"),
    "services_vehicle_service": ("Services", "Vehicle Service"),
    "services_personal_business_finance": ("Services", "Personal Business & Finance"),
    "services_household_maintenance_repair": ("Services", "Household Maintenance & Repair"),
    "services_organizations_charity": ("Services", "Organizations & Charity"),
    "services_other": ("Services", "Other services"),
    "excluded": ("Excluded", "Excluded — counted elsewhere (food/travel/home)"),
    "ignored": ("Excluded", "Ignored (transfers, payments, income)"),
}

# NAICS prefix -> category. Longest matching prefix wins; per-code overrides
# below beat prefixes. Anything unmatched defaults to "excluded" (B2B and
# industrial codes shouldn't appear on consumer statements; when they do via
# retrieval+LLM, their kg/$ still applies but they roll up as excluded from
# the G&S panel unless overridden).
PREFIX_CATEGORY = {
    # -------- goods (retail + manufacturing that surfaces at retail) --------
    "4491": "goods_furniture_appliances",     # furniture & home furnishings retailers
    "44921": "goods_furniture_appliances",    # electronics/appliance retailers (CoolClimate: appliances)
    "444": "goods_furniture_appliances",      # building material & garden retailers (tools/hardware/supplies)
    "337": "goods_furniture_appliances",      # furniture mfg
    "3352": "goods_furniture_appliances",     # household appliance mfg
    "334111": "goods_furniture_appliances",   # computers (CoolClimate: appliances/electronics)
    "334310": "goods_furniture_appliances",   # audio/video equipment
    "325412": "goods_medical",                # pharmaceutical preparation mfg
    "458": "goods_clothing",                  # clothing & accessories retailers
    "315": "goods_clothing",                  # apparel mfg
    "316": "goods_clothing",                  # leather/shoes
    "4591": "goods_entertainment",            # sporting goods/hobby/musical instrument retailers
    "4592": "goods_paper_office_reading",     # book retailers & news dealers
    "4594": "goods_paper_office_reading",     # office supplies & stationery retailers
    "323": "goods_paper_office_reading",      # printing
    "5131": "goods_paper_office_reading",     # newspaper/periodical/book publishers (2022 recode of 5111)
    "33994": "goods_paper_office_reading",    # office supplies mfg
    "4595": "goods_entertainment",            # gift/novelty/used merchandise
    "45911": "goods_entertainment",
    "33992": "goods_entertainment",           # sporting/athletic goods mfg
    "33993": "goods_entertainment",           # dolls, toys, games mfg
    "441330": "goods_auto_parts",             # auto parts retailers
    "326211": "goods_auto_parts",             # tires
    "3391": "goods_medical",                  # medical equipment & supplies mfg
    "456110": "goods_medical",                # pharmacies
    "456130": "goods_medical",                # optical goods retailers
    "45612": "goods_personal_care_cleaning",  # cosmetics/beauty supply retailers
    "3256": "goods_personal_care_cleaning",   # soap/cleaning/toilet preparation mfg
    "3222": "goods_personal_care_cleaning",   # converted paper products (household paper)
    "339910": "goods_clothing",               # jewelry mfg (CoolClimate: jewelry/watches under Clothing)
    "314120": "goods_furniture_appliances",   # curtains/linens/bedding (home furnishings)
    "322230": "goods_paper_office_reading",   # stationery (beats the broad 3222 household-paper prefix)
    "455": "goods_other",                     # department stores, warehouse clubs, general merchandise
    "4599": "goods_other",                    # other miscellaneous retailers
    "45931": "goods_entertainment",           # florists (plants -> Entertainment per CoolClimate)
    # -------- services --------
    "621": "services_health_care",
    "622": "services_health_care",
    "623": "services_health_care",
    "611": "services_education",
    "517": "services_information_communication",  # telecom
    "491": "services_information_communication",  # postal
    "492": "services_information_communication",  # couriers
    "513210": "digital_subscriptions",        # software publishers
    "516": "digital_subscriptions",           # media streaming, social networks
    "518": "digital_subscriptions",           # computing infrastructure/data processing
    "519": "digital_subscriptions",           # web search/libraries/other info services
    "8111": "services_vehicle_service",       # auto repair & maintenance
    "812930": "services_vehicle_service",     # parking lots & garages
    "52": "services_personal_business_finance",   # finance & insurance
    "5411": "services_personal_business_finance", # legal
    "5412": "services_personal_business_finance", # accounting
    "55": "services_personal_business_finance",
    "813930": "services_personal_business_finance",  # labor unions
    "8134": "services_organizations_charity",
    "8131": "services_organizations_charity",
    "8132": "services_organizations_charity",
    "8133": "services_organizations_charity",
    "8139": "services_organizations_charity",
    "5617": "services_household_maintenance_repair",  # services to buildings (cleaning, pest, landscaping)
    "811412": "services_household_maintenance_repair",
    "23": "services_household_maintenance_repair",    # construction/contractors
    "484": "services_household_maintenance_repair",   # moving/trucking
    "493": "services_household_maintenance_repair",   # storage
    "5322": "goods_furniture_appliances",             # consumer goods rental
    "721": "services_other",                  # hotels
    "8121": "services_other",                 # hair/nail/personal care services
    "8122": "services_other",                 # funeral
    "8123": "services_other",                 # laundry/dry cleaning
    "8129": "services_other",                 # pet care, parking->vehicle handled above
    "6244": "services_education",             # child care (day care/preschool -> Education)
    "624": "services_other",                  # individual & family services
    "713": "services_other",                  # amusement/recreation/fitness services
    "712": "services_other",                  # museums
    "711": "services_other",                  # performing arts/spectator sports
    "5615": "services_other",                 # travel agents (service fee portion)
    # -------- excluded from G&S (other calculator sections / not consumption) --------
    "445": "excluded",                        # grocery retail
    "722": "excluded",                        # restaurants & bars
    "311": "excluded",                        # food mfg
    "312": "excluded",                        # beverage mfg
    "457": "excluded",                        # gas stations
    "481": "excluded",                        # air transport
    "482": "excluded",                        # rail
    "483": "excluded",                        # water transport
    "485": "excluded",                        # transit/taxi
    "486": "excluded",
    "487": "excluded",
    "488": "excluded",                        # transport support (airport ops — paid via airfare)
    # Road services consumers buy directly: the Travel tab models fuel burn +
    # vehicle manufacture from miles, so tolls (488490 covers toll road/bridge
    # operations) and towing are counted NOWHERE if excluded — they belong
    # with parking (812930) as vehicle services.
    "488410": "services_vehicle_service",     # motor vehicle towing
    "488490": "services_vehicle_service",     # other road support incl. toll operations
    "221": "excluded",                        # utilities
    "531": "excluded",                        # real estate/rent
    "324": "excluded",                        # petroleum
    "92": "excluded",                         # government
    "11": "excluded",                         # agriculture
    "21": "excluded",                         # mining
}

# Hand enrichment for retrieval: NAICS code -> extra keywords/example merchants.
ENRICH = {
    "445110": "grocery store supermarket Safeway Whole Foods Trader Joes food shopping",
    "488490": "toll road bridge tolls FasTrak EZPass E-ZPass express lanes highway toll booth",
    "488410": "towing tow truck roadside AAA vehicle recovery",
    "722511": "restaurant dining sit-down dinner bistro",
    "722513": "fast food takeout Chipotle McDonald's burger quick service",
    "722515": "coffee shop cafe Starbucks Blue Bottle Peets espresso bakery snack",
    "722410": "bar pub brewery taproom drinks alcohol nightlife",
    "445320": "liquor store wine beer spirits bottle shop",
    "457110": "gas station fuel Shell Chevron petrol",
    "441330": "auto parts store AutoZone O'Reilly car parts tires accessories",
    "811111": "auto repair shop mechanic oil change brakes",
    "811192": "car wash detailing",
    "812930": "parking garage lot meter toll",
    "444110": "home center hardware Home Depot Lowe's tools lumber",
    "444140": "hardware store Ace Hardware tools fasteners",
    "444230": "garden center nursery plants outdoor",
    "449110": "furniture store IKEA couch sofa mattress",
    "449121": "floor covering carpet flooring",
    "449210": "electronics appliance store Best Buy TV refrigerator washer",
    "458110": "clothing store apparel Gap Uniqlo H&M fashion",
    "458210": "shoe store sneakers Nike footwear",
    "458310": "jewelry watches",
    "459110": "sporting goods REI outdoor gear bikes sports equipment",
    "459120": "hobby toy game store crafts Michaels toys",
    "459140": "musical instrument store guitar piano",
    "459210": "book store Barnes Noble books",
    "459410": "office supplies Staples stationery",
    "459510": "used goods thrift store secondhand consignment",
    "455110": "department store Macy's Target Nordstrom",
    "455211": "warehouse club Costco Sam's Club supercenter",
    "459999": "online marketplace general merchandise Amazon Etsy eBay miscellaneous retail",
    "456110": "pharmacy drugstore CVS Walgreens prescription",
    "456120": "cosmetics beauty Sephora Ulta makeup skincare",
    "456130": "optical glasses Warby Parker contacts",
    "621111": "doctor physician office medical visit clinic",
    "621210": "dentist dental orthodontist",
    "621310": "chiropractor",
    "621330": "therapist mental health psychologist counseling",
    "621399": "physical therapy acupuncture optometrist",
    "622110": "hospital medical center emergency",
    "541110": "lawyer attorney legal services",
    "541211": "accountant CPA tax preparation",
    "524113": "life insurance premium",
    "524114": "health insurance medical dental premium",
    "524126": "home auto insurance property casualty premium Geico State Farm",
    "522110": "bank fee banking Chase Wells Fargo",
    "523930": "financial advisor investment management wealth",
    "513210": "software subscription SaaS app license Adobe Microsoft",
    "516210": "streaming Netflix Spotify Hulu Disney+ YouTube Premium social media",
    "518210": "cloud hosting data storage iCloud Dropbox Google One web services",
    "517111": "internet service provider broadband Comcast Xfinity fiber",
    "517112": "phone cell mobile Verizon AT&T T-Mobile wireless",
    "611110": "school tuition K-12 elementary",
    "611310": "university college tuition",
    "611620": "sports lessons swim classes coaching",
    "611610": "art music dance lessons classes",
    "624410": "day care child care preschool daycare",
    "713940": "gym fitness yoga studio ClassPass membership",
    "713910": "golf course country club",
    "713930": "marina yacht club sailing boat slip",
    "812112": "hair salon barber haircut",
    "812113": "nail salon manicure",
    "812191": "spa massage",
    "812320": "dry cleaning laundry",
    "812910": "pet care veterinarian grooming boarding",
    "721110": "hotel motel lodging Marriott Hilton Airbnb stay",
    "561720": "house cleaning maid janitorial",
    "561730": "landscaping lawn care gardener tree service",
    "561740": "carpet cleaning",
    "238220": "plumber HVAC heating cooling contractor",
    "238210": "electrician electrical contractor",
    "236118": "remodeling renovation contractor home improvement",
    "813110": "church temple religious donation",
    "813211": "charity donation nonprofit foundation GoFundMe",
    "813410": "club membership civic social organization",
    "485310": "rideshare Uber Lyft taxi",
    "485110": "public transit bus subway BART Muni",
    "481111": "airline flight United Delta Southwest",
    "531110": "rent apartment landlord lessor",
    "221122": "electric utility power PG&E",
    "221210": "natural gas utility",
    "221310": "water utility",
    "562111": "garbage waste collection",
    "491110": "post office USPS postage shipping",
    "492110": "FedEx UPS courier package delivery",
    "339920": "sporting goods manufacturing exercise equipment",
    "332216": "hand tools",
    # ---- commodity (31-33) codes so single-category goods brands can be
    # mapped to "what was bought" directly, per the design note ----
    "315250": "apparel manufacturing clothing brand shirts pants dresses jacket Patagonia Everlane",
    "316210": "footwear manufacturing shoes sneakers boots brand Nike Allbirds",
    "339910": "jewelry watches manufacturing rings",
    "337121": "upholstered furniture manufacturing sofa couch armchair",
    "337122": "wood furniture manufacturing table chairs dresser bed frame",
    "314120": "curtains linens bedding sheets towels manufacturing",
    "335220": "household appliance manufacturing refrigerator washer dryer stove vacuum",
    "334111": "computer manufacturing laptop desktop Apple Dell Lenovo",
    "334310": "audio video equipment manufacturing speakers headphones TV Sonos",
    "339930": "toys dolls games manufacturing Lego",
    "325412": "pharmaceutical medication drugs manufacturing prescription",
    "325620": "toiletries cosmetics shampoo skincare sunscreen manufacturing",
    "325611": "soap detergent dish laundry cleaning products manufacturing Method Grove Seventh Generation",
    "323117": "book printing publishing books",
}


# Retail NAICS are *margin* industries in USEEIO — their factor covers only
# the emissions of running the store (~0.09 kg/$), not making the goods sold.
# For consumer purchases the right factor is the purchased commodity's
# "with margins" factor (per purchaser-price dollar, manufacturing included).
# Each retail code gets a weighted commodity basket; its index factor is
# replaced by the basket average. Secondhand retail (4595x) keeps the margin
# factor on purpose: no new manufacturing.
RETAIL_BASKETS = {
    "458110": [("315250", 0.6), ("316210", 0.2), ("339910", 0.2)],       # clothing: apparel, footwear, jewelry
    "458210": [("316210", 1.0)],                                          # shoes
    "458310": [("339910", 1.0)],                                          # jewelry
    "458320": [("316990", 1.0)],                                          # luggage/leather goods
    "449110": [("337121", 0.35), ("337122", 0.35), ("314120", 0.15), ("337910", 0.15)],  # furniture, textiles, mattresses
    "449121": [("314120", 1.0)],                                          # floor coverings ~ textile furnishings
    "449129": [("337122", 0.5), ("327110", 0.25), ("314120", 0.25)],      # home furnishings: wood, pottery/dishes, linens
    "449210": [("335220", 0.35), ("334310", 0.25), ("334111", 0.4)],      # appliances, AV, computers
    "459110": [("339920", 1.0)],                                          # sporting goods
    "459120": [("339930", 0.7), ("339992", 0.3)],                         # toys/games, musical instruments
    "459140": [("339992", 1.0)],
    "459210": [("323117", 1.0)],                                          # books
    "459410": [("322230", 0.6), ("339940", 0.4)],                         # stationery, office supplies
    "456110": [("325412", 0.8), ("325620", 0.2)],                         # pharmacy: drugs + front of store
    "456120": [("325620", 1.0)],                                          # cosmetics
    "456130": [("339115", 1.0)],                                          # optical
    "444110": [("332216", 0.25), ("325510", 0.2), ("321113", 0.25), ("335220", 0.15), ("326199", 0.15)],  # home centers
    "444140": [("332216", 0.6), ("326199", 0.4)],                         # hardware stores
    "444230": [("111422", 0.6), ("332216", 0.4)],                         # garden centers (outdoor power ~ tools)
    "444240": [("111422", 0.7), ("332216", 0.3)],
    "441330": [("326211", 0.4), ("336310", 0.6)],                         # auto parts: tires, engine parts
    "459310": [("111422", 1.0)],                                          # florists
}
# General merchandise (department stores, warehouse clubs, online marketplaces,
# gift shops) get a broad household-goods basket.
GENERAL_MERCH_BASKET = [("315250", 0.15), ("337122", 0.15), ("334111", 0.1), ("335210", 0.1),
                        ("339930", 0.1), ("339920", 0.1), ("325620", 0.15), ("322230", 0.15)]
GENERAL_MERCH_CODES = {"455110", "455211", "455219", "459999", "459420", "459991"}


def category_for(code: str) -> str:
    for length in range(6, 1, -1):
        cat = PREFIX_CATEGORY.get(code[:length])
        if cat:
            return cat
    return "excluded"


# For 2022 codes spanning multiple 2017 rows, pick the 2017 row a household
# purchase actually means. Unlisted duplicates take the first row, which for
# the store+online merges is the physical-store variant.
DUP_PICK = {
    "516210": "515210",  # streaming subscriptions -> cable/subscription programming, not radio networks
    "335910": "335912",  # household batteries are primary (AA/AAA), not industrial storage
}

DATASET = "EPA Supply Chain GHG Emission Factors v1.3.0 (kg CO2e / 2022 USD, purchaser price)"


def build() -> dict:
    rows = list(csv.DictReader(FACTORS_CSV.open()))
    by_2022: dict[str, list[dict]] = {}
    for r in rows:
        by_2022.setdefault(r["2022 NAICS Code"].strip(), []).append(r)

    seen = {}
    for code, group in by_2022.items():
        r = group[0]
        if code in DUP_PICK:
            r = next((g for g in group if g["2017 NAICS Code"].strip() == DUP_PICK[code]), r)
        title = r["2022 NAICS Title"].strip()
        raw = r["Supply Chain Emission Factors with Margins"].strip()
        if not raw:
            continue  # a few codes lack factors
        factor = float(raw)
        factor_production = float(r["Supply Chain Emission Factors without Margins"].strip() or 0)
        factor_margins = float(r["Margins of Supply Chain Emission Factors"].strip() or 0)
        cat = category_for(code)
        search = title
        if code in ENRICH:
            search += " — " + ENRICH[code]
        entry = {
            "code": code,
            "title": title,
            "factor": factor,                      # kg CO2e / 2022 USD, purchaser price w/ margins
            "factor_production": factor_production,  # production chain only
            "factor_margins": factor_margins,        # retail/wholesale/transport margin layer
            "naics2017": r["2017 NAICS Code"].strip(),   # the factors' native (unique) key
            "useeio": r["Reference USEEIO Code"].strip(),  # real model resolution
            "category": cat,
            "search_text": search,
        }
        # Direct-to-consumer digital: EPA margins on these rows model boxed
        # software / physical media — an artifact for subscriptions and
        # downloads. Charge production only.
        if cat == "digital_subscriptions" and factor_margins > 0:
            entry["factor"] = factor_production
            entry["factor_margins"] = 0.0
            entry["factor_note"] = "digital: production only (EPA margins model boxed media)"
        seen[code] = entry

    # Retail rows price the retail markup only (the store's own operations) —
    # they know nothing about the goods on the shelf. Re-price each consumer
    # retail code as a weighted commodity basket: the blended with-margins
    # factor of what's actually bought there. The commodities' margin factors
    # already contain the store/wholesale/transport layer, so no separate
    # retail component is kept anywhere.
    n_basket = 0
    for code, entry in seen.items():
        basket = RETAIL_BASKETS.get(code) or (GENERAL_MERCH_BASKET if code in GENERAL_MERCH_CODES else None)
        if not basket:
            continue
        avail = [(c, w) for c, w in basket if c in seen]
        if not avail:
            continue
        total_w = sum(w for _, w in avail)
        entry["factor"] = round(sum(seen[c]["factor"] * w for c, w in avail) / total_w, 4)
        entry["factor_production"] = round(sum(seen[c]["factor_production"] * w for c, w in avail) / total_w, 4)
        entry["factor_margins"] = round(sum(seen[c]["factor_margins"] * w for c, w in avail) / total_w, 4)
        entry["factor_note"] = "commodity basket (manufacturing incl.)"
        entry["basket"] = [{"naics": c, "weight": round(w / total_w, 4)} for c, w in avail]
        n_basket += 1

    # Utilities have no EPA factors (physical-unit territory — the Home tab
    # handles them via kWh/therms). Synthetic factor-0 entries let PG&E-style
    # payments classify cleanly to an excluded utility code instead of
    # landing somewhere random.
    for code, title in (("221122", "Electric Power Distribution"),
                        ("221210", "Natural Gas Distribution"),
                        ("221310", "Water Supply and Irrigation Systems"),
                        ("221320", "Sewage Treatment Facilities")):
        if code not in seen:
            search = title + ((" — " + ENRICH[code]) if code in ENRICH else "")
            seen[code] = {"code": code, "title": title, "factor": 0.0,
                          "factor_production": 0.0, "factor_margins": 0.0,
                          "naics2017": code, "useeio": None, "category": "excluded",
                          "factor_note": "no EPA factor — enter physical usage in the Home tab",
                          "search_text": search}

    index = {
        "dataset": DATASET,
        "categories": {k: {"section": s, "label": l} for k, (s, l) in CATEGORIES.items()},
        "entries": list(seen.values()),
    }
    INDEX_FILE.write_text(json.dumps(index, indent=1))
    n_enriched = sum(1 for e in seen.values() if e["code"] in ENRICH)
    print(f"Built {INDEX_FILE}: {len(seen)} codes, {n_enriched} enriched, {n_basket} retail codes basket-priced")
    return index


if __name__ == "__main__":
    build()
