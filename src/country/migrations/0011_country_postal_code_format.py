"""Add ``Country.postal_code_pattern`` / ``postal_code_example`` and seed them.

Purely additive and deploy-safe: both columns carry ``db_default=""``,
so the previous release's INSERTs (which do not list them) keep working
while this PreSync migration has already run.

The seed is Google's Address Data Service, copied verbatim on
2026-09-25: for every ISO 3166-1 country, the ``zip`` regex and the
first ``zipex`` example of
https://www.gstatic.com/chrome/autofill/libaddressinput/chromium-i18n/ssl-address/data/<CC>
(e.g. ``.../data/GR`` gives the ``GR`` row below).
Countries the service lists without a ``zip`` key have no postal-code
system and are absent here; MY, KP and SY answered 404 and are absent too.

The table covers every country so it reaches whatever rows an
environment has (``country`` is a SHARED_APPS table curated per
environment). It only fills a row whose pattern is still blank, the
same non-destructive rule as ``0010_seed_default_country``: an operator
who has already set a format keeps it. Reverse is a no-op for the same
reason.
"""

import core.validators.address
from django.db import migrations, models


# alpha-2 -> (Google ``zip`` regex, first Google ``zipex`` example)
GOOGLE_POSTAL_FORMATS: dict[str, tuple[str, str]] = {
    "AD": ("AD[1-7]0\\d", "AD100"),
    "AF": ("\\d{4}", "1001"),
    "AI": ("(?:AI-)?2640", "2640"),
    "AL": ("\\d{4}", "1001"),
    "AM": ("(?:37)?\\d{4}", "375010"),
    "AR": ("[A-HJ-NP-Z]\\d{4}[A-Z]{3}", "C1070AAM"),
    "AS": ("(96799)(?:[ \\-](\\d{4}))?", "96799"),
    "AT": ("\\d{4}", "1010"),
    "AU": ("\\d{4}", "2060"),
    "AX": ("22\\d{3}", "22150"),
    "AZ": ("\\d{4}", "1000"),
    "BA": ("\\d{5}", "71000"),
    "BB": ("BB\\d{5}", "BB23026"),
    "BD": ("\\d{4}", "1340"),
    "BE": ("\\d{4}", "4000"),
    "BG": ("\\d{4}", "1000"),
    "BH": ("(?:^|\\b)(?:1[0-2]|[1-9])\\d{2}(?:$|\\b)", "317"),
    "BL": ("9[78][01]\\d{2}", "97100"),
    "BM": ("[A-Z]{2} ?[A-Z0-9]{2}", "FL 07"),
    "BN": ("[A-Z]{2} ?\\d{4}", "BT2328"),
    "BR": ("\\d{5}-?\\d{3}", "40301-110"),
    "BT": ("\\d{5}", "11001"),
    "BY": ("\\d{6}", "223016"),
    "CA": ("[ABCEGHJKLMNPRSTVXY]\\d[ABCEGHJ-NPRSTV-Z] ?\\d[ABCEGHJ-NPRSTV-Z]\\d", "H3Z 2Y7"),
    "CC": ("6799", "6799"),
    "CH": ("\\d{4}", "2544"),
    "CL": ("\\d{7}", "8340457"),
    "CN": ("\\d{6}", "266033"),
    "CO": ("\\d{6}", "111221"),
    "CR": ("\\d{4,5}|\\d{3}-\\d{4}", "1000"),
    "CU": ("\\d{5}", "10700"),
    "CV": ("\\d{4}", "7600"),
    "CX": ("6798", "6798"),
    "CY": ("\\d{4}", "2008"),
    "CZ": ("\\d{3} ?\\d{2}", "100 00"),
    "DE": ("\\d{5}", "26133"),
    "DK": ("\\d{4}", "8660"),
    "DO": ("\\d{5}", "11903"),
    "DZ": ("\\d{5}", "40304"),
    "EC": ("\\d{6}", "090105"),
    "EE": ("\\d{5}", "69501"),
    "EG": ("\\d{7}|\\d{5}", "4460232"),
    "EH": ("\\d{5}", "70000"),
    "ES": ("\\d{5}", "28039"),
    "ET": ("\\d{4}", "1000"),
    "FI": ("\\d{5}", "00550"),
    "FK": ("FIQQ 1ZZ", "FIQQ 1ZZ"),
    "FM": ("(9694[1-4])(?:[ \\-](\\d{4}))?", "96941"),
    "FO": ("\\d{3}", "100"),
    "FR": ("\\d{2} ?\\d{3}", "33380"),
    "GB": ("GIR ?0AA|(?:(?:AB|AL|B|BA|BB|BD|BF|BH|BL|BN|BR|BS|BT|BX|CA|CB|CF|CH|CM|CO|CR|CT|CV|CW|DA|DD|DE|DG|DH|DL|DN|DT|DY|E|EC|EH|EN|EX|FK|FY|G|GL|GY|GU|HA|HD|HG|HP|HR|HS|HU|HX|IG|IM|IP|IV|JE|KA|KT|KW|KY|L|LA|LD|LE|LL|LN|LS|LU|M|ME|MK|ML|N|NE|NG|NN|NP|NR|NW|OL|OX|PA|PE|PH|PL|PO|PR|RG|RH|RM|S|SA|SE|SG|SK|SL|SM|SN|SO|SP|SR|SS|ST|SW|SY|TA|TD|TF|TN|TQ|TR|TS|TW|UB|W|WA|WC|WD|WF|WN|WR|WS|WV|YO|ZE)(?:\\d[\\dA-Z]? ?\\d[ABD-HJLN-UW-Z]{2}))|BFPO ?\\d{1,4}", "EC1Y 8SY"),
    "GE": ("\\d{4}", "0101"),
    "GF": ("9[78]3\\d{2}", "97300"),
    "GG": ("GY\\d[\\dA-Z]? ?\\d[ABD-HJLN-UW-Z]{2}", "GY1 1AA"),
    "GI": ("GX11 1AA", "GX11 1AA"),
    "GL": ("39\\d{2}", "3900"),
    "GN": ("\\d{3}", "001"),
    "GP": ("9[78][01]\\d{2}", "97100"),
    "GR": ("\\d{3} ?\\d{2}", "151 24"),
    "GS": ("SIQQ 1ZZ", "SIQQ 1ZZ"),
    "GT": ("\\d{5}", "09001"),
    "GU": ("(969(?:[12]\\d|3[12]))(?:[ \\-](\\d{4}))?", "96910"),
    "GW": ("\\d{4}", "1000"),
    "HM": ("\\d{4}", "7050"),
    "HN": ("\\d{5}", "31301"),
    "HR": ("\\d{5}", "10000"),
    "HT": ("\\d{4}", "6120"),
    "HU": ("\\d{4}", "1037"),
    "ID": ("\\d{5}", "40115"),
    "IE": ("[\\dA-Z]{3} ?[\\dA-Z]{4}", "A65 F4E2"),
    "IL": ("\\d{5}(?:\\d{2})?", "9614303"),
    "IM": ("IM\\d[\\dA-Z]? ?\\d[ABD-HJLN-UW-Z]{2}", "IM2 1AA"),
    "IN": ("\\d{6}", "110034"),
    "IO": ("BBND 1ZZ", "BBND 1ZZ"),
    "IQ": ("\\d{5}", "31001"),
    "IR": ("\\d{5}-?\\d{5}", "11936-12345"),
    "IS": ("\\d{3}", "320"),
    "IT": ("\\d{5}", "00144"),
    "JE": ("JE\\d[\\dA-Z]? ?\\d[ABD-HJLN-UW-Z]{2}", "JE1 1AA"),
    "JO": ("\\d{5}", "11937"),
    "JP": ("\\d{3}-?\\d{4}", "154-0023"),
    "KE": ("\\d{5}", "20100"),
    "KG": ("\\d{6}", "720001"),
    "KH": ("\\d{5,6}", "120101"),
    "KR": ("\\d{5}", "03051"),
    "KW": ("\\d{5}", "54541"),
    "KY": ("KY\\d-\\d{4}", "KY1-1100"),
    "KZ": ("\\d{6}", "040900"),
    "LA": ("\\d{5}", "01160"),
    "LB": ("(?:\\d{4})(?: ?(?:\\d{4}))?", "2038 3054"),
    "LI": ("948[5-9]|949[0-8]", "9496"),
    "LK": ("\\d{5}", "20000"),
    "LR": ("\\d{4}", "1000"),
    "LS": ("\\d{3}", "100"),
    "LT": ("\\d{5}", "04340"),
    "LU": ("\\d{4}", "4750"),
    "LV": ("LV-\\d{4}", "LV-1073"),
    "MA": ("\\d{5}", "53000"),
    "MC": ("980\\d{2}", "98000"),
    "MD": ("\\d{4}", "2012"),
    "ME": ("8\\d{4}", "81257"),
    "MF": ("9[78][01]\\d{2}", "97100"),
    "MG": ("\\d{3}", "501"),
    "MH": ("(969[67]\\d)(?:[ \\-](\\d{4}))?", "96960"),
    "MK": ("\\d{4}", "1314"),
    "MM": ("\\d{5}", "11181"),
    "MN": ("\\d{5}", "65030"),
    "MP": ("(9695[012])(?:[ \\-](\\d{4}))?", "96950"),
    "MQ": ("9[78]2\\d{2}", "97220"),
    "MT": ("[A-Z]{3} ?\\d{2,4}", "NXR 01"),
    "MU": ("\\d{3}(?:\\d{2}|[A-Z]{2}\\d{3})", "42602"),
    "MV": ("\\d{5}", "20026"),
    "MX": ("\\d{5}", "02860"),
    "MZ": ("\\d{4}", "1102"),
    "NA": ("\\d{5}", "10001"),
    "NC": ("988\\d{2}", "98814"),
    "NE": ("\\d{4}", "8001"),
    "NF": ("2899", "2899"),
    "NG": ("\\d{6}", "930283"),
    "NI": ("\\d{5}", "52000"),
    "NL": ("[1-9]\\d{3} ?(?:[A-RT-Z][A-Z]|S[BCE-RT-Z])", "1234 AB"),
    "NO": ("\\d{4}", "0025"),
    "NP": ("\\d{5}", "44601"),
    "NZ": ("\\d{4}", "6001"),
    "OM": ("(?:PC )?\\d{3}", "133"),
    "PE": ("(?:LIMA \\d{1,2}|CALLAO 0?\\d)|[0-2]\\d{4}", "LIMA 23"),
    "PF": ("987\\d{2}", "98709"),
    "PG": ("\\d{3}", "111"),
    "PH": ("\\d{4}", "1008"),
    "PK": ("\\d{5}", "44000"),
    "PL": ("\\d{2}-\\d{3}", "00-950"),
    "PM": ("9[78]5\\d{2}", "97500"),
    "PN": ("PCRN 1ZZ", "PCRN 1ZZ"),
    "PR": ("(00[679]\\d{2})(?:[ \\-](\\d{4}))?", "00930"),
    "PT": ("\\d{4}-\\d{3}", "2725-079"),
    "PW": ("(969(?:39|40))(?:[ \\-](\\d{4}))?", "96940"),
    "PY": ("\\d{6}", "001001"),
    "RE": ("9[78]4\\d{2}", "97400"),
    "RO": ("\\d{6}", "060274"),
    "RS": ("\\d{5,6}", "106314"),
    "RU": ("\\d{6}", "247112"),
    "SA": ("\\d{5}", "11564"),
    "SD": ("\\d{5}", "11042"),
    "SE": ("\\d{3} ?\\d{2}", "11455"),
    "SG": ("\\d{6}", "546080"),
    "SH": ("(?:ASCN|STHL) 1ZZ", "STHL 1ZZ"),
    "SI": ("\\d{4}", "4000"),
    "SJ": ("\\d{4}", "9170"),
    "SK": ("\\d{3} ?\\d{2}", "010 01"),
    "SM": ("4789\\d", "47890"),
    "SN": ("\\d{5}", "12500"),
    "SO": ("[A-Z]{2} ?\\d{5}", "JH 09010"),
    "SV": ("[1-3][1-7][0-2]\\d", "1101"),
    "SZ": ("[HLMS]\\d{3}", "H100"),
    "TC": ("TKCA 1ZZ", "TKCA 1ZZ"),
    "TH": ("\\d{5}", "10150"),
    "TJ": ("\\d{6}", "735450"),
    "TM": ("\\d{6}", "744000"),
    "TN": ("\\d{4}", "1002"),
    "TR": ("\\d{5}", "01960"),
    "TT": ("\\d{6}", "500234"),
    "TW": ("\\d{3}(?:\\d{2,3})?", "104"),
    "TZ": ("\\d{4,5}", "6090"),
    "UA": ("\\d{5}", "15432"),
    "UM": ("96898", "96898"),
    "US": ("(\\d{5})(?:[ \\-](\\d{4}))?", "95014"),
    "UY": ("\\d{5}", "11600"),
    "UZ": ("\\d{6}", "702100"),
    "VA": ("00120", "00120"),
    "VC": ("VC\\d{4}", "VC0100"),
    "VE": ("\\d{4}", "1010"),
    "VG": ("VG\\d{4}", "VG1110"),
    "VI": ("(008(?:(?:[0-4]\\d)|(?:5[01])))(?:[ \\-](\\d{4}))?", "00802-1222"),
    "VN": ("\\d{5}\\d?", "70010"),
    "WF": ("986\\d{2}", "98600"),
    "YT": ("976\\d{2}", "97600"),
    "ZA": ("\\d{4}", "0083"),
    "ZM": ("\\d{5}", "50100"),
}


def seed_postal_code_formats(apps, schema_editor):
    Country = apps.get_model("country", "Country")
    db_alias = schema_editor.connection.alias

    for country in Country.objects.using(db_alias).filter(
        alpha_2__in=list(GOOGLE_POSTAL_FORMATS), postal_code_pattern=""
    ):
        pattern, example = GOOGLE_POSTAL_FORMATS[country.alpha_2]
        country.postal_code_pattern = pattern
        country.postal_code_example = example
        country.save(
            update_fields=["postal_code_pattern", "postal_code_example"]
        )


class Migration(migrations.Migration):

    dependencies = [
        ('country', '0010_seed_default_country'),
    ]

    operations = [
        migrations.AddField(
            model_name='country',
            name='postal_code_example',
            field=models.CharField(blank=True, db_default='', default='', help_text='A valid postcode shown to shoppers, e.g. 151 24.', max_length=50, verbose_name='Postal Code Example'),
        ),
        migrations.AddField(
            model_name='country',
            name='postal_code_pattern',
            field=models.CharField(blank=True, db_default='', default='', help_text='Regular expression a whole postcode must match, e.g. \\d{3} ?\\d{2} for Greece. Blank disables the format check.', max_length=1000, validators=[core.validators.address.validate_postal_code_pattern], verbose_name='Postal Code Pattern'),
        ),
        migrations.RunPython(
            seed_postal_code_formats,
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
