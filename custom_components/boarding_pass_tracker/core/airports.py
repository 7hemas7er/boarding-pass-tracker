"""airports: IATA code -> name to display, in Italian, in the route of a flight.

AviationStack gives the airport name ("Leonardo Da Vinci (Fiumicino)", "Schiphol"), not the city. This table has
the Italian city name and, where a city has more than one airport, the airport too ("Roma Fiumicino",
"Londra Stansted"). A missing code is not an error: flights.city falls back to the AviationStack name, then to
the code.
"""

CITIES = {
    # Italy
    "FCO": "Roma Fiumicino", "CIA": "Roma Ciampino", "MXP": "Milano Malpensa", "LIN": "Milano Linate",
    "BGY": "Bergamo", "BLQ": "Bologna", "VCE": "Venezia", "TSF": "Treviso", "VRN": "Verona", "TRN": "Torino",
    "GOA": "Genova", "PSA": "Pisa", "FLR": "Firenze", "NAP": "Napoli", "QSR": "Salerno", "BRI": "Bari",
    "BDS": "Brindisi", "CTA": "Catania", "CIY": "Comiso", "PMO": "Palermo", "TPS": "Trapani", "CAG": "Cagliari",
    "OLB": "Olbia", "AHO": "Alghero", "SUF": "Lamezia Terme", "REG": "Reggio Calabria", "CRV": "Crotone",
    "PEG": "Perugia", "AOI": "Ancona", "PSR": "Pescara", "TRS": "Trieste", "BZO": "Bolzano", "CUF": "Cuneo",
    "RMI": "Rimini", "FRL": "Forlì", "PMF": "Parma", "LMP": "Lampedusa", "PNL": "Pantelleria",
    # Europe
    "AMS": "Amsterdam", "EIN": "Eindhoven", "RTM": "Rotterdam", "LHR": "Londra Heathrow", "LGW": "Londra Gatwick",
    "STN": "Londra Stansted", "LTN": "Londra Luton", "LCY": "Londra City", "SEN": "Londra Southend",
    "MAN": "Manchester", "BHX": "Birmingham", "BRS": "Bristol", "LPL": "Liverpool", "NCL": "Newcastle",
    "EDI": "Edimburgo", "GLA": "Glasgow", "BFS": "Belfast", "DUB": "Dublino", "ORK": "Cork", "SNN": "Shannon",
    "CDG": "Parigi Charles de Gaulle", "ORY": "Parigi Orly", "BVA": "Parigi Beauvais", "NCE": "Nizza",
    "MRS": "Marsiglia", "LYS": "Lione", "TLS": "Tolosa", "BOD": "Bordeaux", "NTE": "Nantes", "MPL": "Montpellier",
    "BIA": "Bastia", "AJA": "Ajaccio", "BRU": "Bruxelles", "CRL": "Bruxelles Charleroi", "LUX": "Lussemburgo",
    "FRA": "Francoforte", "HHN": "Francoforte Hahn", "MUC": "Monaco di Baviera", "BER": "Berlino", "HAM": "Amburgo",
    "DUS": "Düsseldorf", "NRN": "Düsseldorf Weeze", "CGN": "Colonia", "STR": "Stoccarda", "NUE": "Norimberga",
    "HAJ": "Hannover", "LEJ": "Lipsia", "DRS": "Dresda", "FMM": "Memmingen", "VIE": "Vienna", "SZG": "Salisburgo",
    "INN": "Innsbruck", "ZRH": "Zurigo", "GVA": "Ginevra", "BSL": "Basilea", "MAD": "Madrid", "BCN": "Barcellona",
    "GRO": "Girona", "REU": "Reus", "VLC": "Valencia", "ALC": "Alicante", "AGP": "Malaga", "SVQ": "Siviglia",
    "BIO": "Bilbao", "PMI": "Palma di Maiorca", "IBZ": "Ibiza", "MAH": "Minorca", "TFS": "Tenerife Sud",
    "TFN": "Tenerife Nord", "LPA": "Gran Canaria", "ACE": "Lanzarote", "FUE": "Fuerteventura", "LIS": "Lisbona",
    "OPO": "Porto", "FAO": "Faro", "FNC": "Madeira", "PDL": "Ponta Delgada", "CPH": "Copenaghen", "BLL": "Billund",
    "OSL": "Oslo", "BGO": "Bergen", "ARN": "Stoccolma Arlanda", "BMA": "Stoccolma Bromma",
    "NYO": "Stoccolma Skavsta", "GOT": "Göteborg", "HEL": "Helsinki", "KEF": "Reykjavík", "RIX": "Riga",
    "TLL": "Tallinn", "VNO": "Vilnius", "WAW": "Varsavia", "WMI": "Varsavia Modlin", "KRK": "Cracovia",
    "GDN": "Danzica", "WRO": "Breslavia", "KTW": "Katowice", "POZ": "Poznań", "PRG": "Praga", "BUD": "Budapest",
    "BTS": "Bratislava", "LJU": "Lubiana", "ZAG": "Zagabria", "SPU": "Spalato", "DBV": "Dubrovnik", "PUY": "Pola",
    "ZAD": "Zara", "BEG": "Belgrado", "SJJ": "Sarajevo", "TGD": "Podgorica", "TIV": "Tivat", "TIA": "Tirana",
    "SKP": "Skopje", "SOF": "Sofia", "VAR": "Varna", "BOJ": "Burgas", "OTP": "Bucarest", "CLJ": "Cluj-Napoca",
    "TSR": "Timișoara", "IAS": "Iași", "KIV": "Chișinău", "ATH": "Atene", "SKG": "Salonicco", "HER": "Heraklion",
    "CHQ": "Chania", "RHO": "Rodi", "CFU": "Corfù", "JTR": "Santorini", "JMK": "Mykonos", "KGS": "Kos",
    "ZTH": "Zante", "EFL": "Cefalonia", "LCA": "Larnaca", "PFO": "Paphos", "MLA": "Malta", "IST": "Istanbul",
    "SAW": "Istanbul Sabiha Gökçen", "AYT": "Antalya", "ESB": "Ankara", "ADB": "Smirne", "DLM": "Dalaman",
    "BJV": "Bodrum", "KBP": "Kiev", "TBS": "Tbilisi", "EVN": "Erevan", "GYD": "Baku",
    # Middle East and Africa
    "DXB": "Dubai", "DWC": "Dubai World Central", "AUH": "Abu Dhabi", "DOH": "Doha", "BAH": "Bahrein",
    "KWI": "Kuwait", "MCT": "Mascate", "RUH": "Riad", "JED": "Gedda", "AMM": "Amman", "BEY": "Beirut",
    "TLV": "Tel Aviv", "CAI": "Il Cairo", "HRG": "Hurghada", "SSH": "Sharm el-Sheikh", "RMF": "Marsa Alam",
    "CMN": "Casablanca", "RAK": "Marrakech", "AGA": "Agadir", "TNG": "Tangeri", "FEZ": "Fès", "TUN": "Tunisi",
    "DJE": "Djerba", "MIR": "Monastir", "NBE": "Enfidha", "ALG": "Algeri", "ADD": "Addis Abeba", "NBO": "Nairobi",
    "MBA": "Mombasa", "ZNZ": "Zanzibar", "JRO": "Kilimangiaro", "DAR": "Dar es Salaam", "JNB": "Johannesburg",
    "CPT": "Città del Capo", "MRU": "Mauritius", "SEZ": "Seychelles", "DSS": "Dakar", "LOS": "Lagos",
    "ACC": "Accra", "SID": "Sal", "BVC": "Boa Vista",
    # Americas
    "JFK": "New York JFK", "EWR": "New York Newark", "LGA": "New York LaGuardia", "BOS": "Boston",
    "IAD": "Washington Dulles", "DCA": "Washington", "ORD": "Chicago O'Hare", "ATL": "Atlanta", "MIA": "Miami",
    "FLL": "Fort Lauderdale", "MCO": "Orlando", "LAX": "Los Angeles", "SFO": "San Francisco", "SEA": "Seattle",
    "LAS": "Las Vegas", "DFW": "Dallas", "IAH": "Houston", "DEN": "Denver", "PHL": "Filadelfia", "DTW": "Detroit",
    "MSP": "Minneapolis", "CLT": "Charlotte", "PHX": "Phoenix", "YYZ": "Toronto", "YUL": "Montréal",
    "YVR": "Vancouver", "MEX": "Città del Messico", "CUN": "Cancún", "HAV": "L'Avana", "PUJ": "Punta Cana",
    "SDQ": "Santo Domingo", "GRU": "San Paolo", "GIG": "Rio de Janeiro", "EZE": "Buenos Aires", "BOG": "Bogotá",
    "LIM": "Lima", "SCL": "Santiago del Cile", "PTY": "Panamá",
    # Asia and Oceania
    "HKG": "Hong Kong", "PEK": "Pechino", "PKX": "Pechino Daxing", "PVG": "Shanghai", "NRT": "Tokyo Narita",
    "HND": "Tokyo Haneda", "KIX": "Osaka", "ICN": "Seul", "SIN": "Singapore", "BKK": "Bangkok",
    "DMK": "Bangkok Don Mueang", "HKT": "Phuket", "KUL": "Kuala Lumpur", "CGK": "Giacarta", "DPS": "Bali",
    "MNL": "Manila", "DEL": "Delhi", "BOM": "Mumbai", "MLE": "Malé", "CMB": "Colombo", "KTM": "Kathmandu",
    "SGN": "Ho Chi Minh", "HAN": "Hanoi", "TPE": "Taipei", "SYD": "Sydney", "MEL": "Melbourne", "AKL": "Auckland",
}
