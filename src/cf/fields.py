"""CoolClimate calculator target fields (advanced Shopping tab), in the
order they appear in the calculator UI."""

# key -> (section, label shown in the calculator)
FIELDS = {
    "goods_furniture_appliances": ("Goods", "Furniture & Appliances"),
    "goods_clothing": ("Goods", "Clothing"),
    "goods_entertainment": ("Goods", "Entertainment"),
    "goods_paper_office_reading": ("Goods", "Paper, Office, & Reading"),
    "goods_personal_care_cleaning": ("Goods", "Personal Care & Cleaning"),
    "goods_auto_parts": ("Goods", "Auto Parts"),
    "goods_medical": ("Goods", "Medical"),
    "services_health_care": ("Services", "Health Care"),
    "services_education": ("Services", "Education"),
    "services_information_communication": ("Services", "Information and Communication"),
    "services_vehicle_service": ("Services", "Vehicle Service"),
    "services_personal_business_finance": ("Services", "Personal Business & Finance"),
    "services_household_maintenance_repair": ("Services", "Household Maintenance & Repair"),
    "services_organizations_charity": ("Services", "Organizations & Charity"),
    "services_other": ("Services", "Other services"),
}

EXCLUDE = "exclude"

# Our field key -> the calculator's internal input name (redux state /
# localStorage), used to prefill the CoolClimate app.
CALC_KEYS = {
    "goods_furniture_appliances": "input_footprint_shopping_goods_furnitureappliances",
    "goods_clothing": "input_footprint_shopping_goods_clothing",
    "goods_entertainment": "input_footprint_shopping_goods_other_entertainment",
    "goods_paper_office_reading": "input_footprint_shopping_goods_other_office",
    "goods_personal_care_cleaning": "input_footprint_shopping_goods_other_personalcare",
    "goods_auto_parts": "input_footprint_shopping_goods_other_autoparts",
    "goods_medical": "input_footprint_shopping_goods_other_medical",
    "services_health_care": "input_footprint_shopping_services_healthcare",
    "services_education": "input_footprint_shopping_services_education",
    "services_information_communication": "input_footprint_shopping_services_communications",
    "services_vehicle_service": "input_footprint_shopping_services_vehicleservices",
    "services_personal_business_finance": "input_footprint_shopping_services_finance",
    "services_household_maintenance_repair": "input_footprint_shopping_services_household",
    "services_organizations_charity": "input_footprint_shopping_services_charity",
    "services_other": "input_footprint_shopping_services_miscservices",
}

# Flip the Shopping panels into Advanced ($/category) mode.
CALC_MODE_FLAGS = {
    "input_footprint_shopping_goods_type": 1,
    "input_footprint_shopping_goods_other_type": 1,
    "input_footprint_shopping_services_type": 1,
}
