"""Bilingual text lookup for WhatsApp replies (English + Afrikaans)."""

_STRINGS = {
    # --- Menus ---
    "main_menu_greeting": {
        "en": "Hi {name} \U0001f44b How can I help with your account today?",
        "af": "Hallo {name} \U0001f44b Waarmee kan ek jou vandag met jou rekening help?",
    },
    "main_menu_greeting_anon": {
        "en": "Hi \U0001f44b How can I help with your account today?",
        "af": "Hallo \U0001f44b Waarmee kan ek jou vandag met jou rekening help?",
    },
    "main_menu_1": {
        "en": "Account summary",
        "af": "Rekeningopsomming",
    },
    "main_menu_2": {
        "en": "Balance and unpaid invoices",
        "af": "Balans en oop fakture",
    },
    "main_menu_3": {
        "en": "Invoice or statement",
        "af": "Faktuur of staat",
    },
    "main_menu_4": {
        "en": "Support",
        "af": "Ondersteuning",
    },
    "main_menu_5": {
        "en": "Connection check",
        "af": "Konneksietoets",
    },

    # --- Document sub-menu ---
    "document_menu_preamble": {
        "en": "Sure \u2014 what would you like me to send?",
        "af": "Seker \u2014 wat wil jy h\u00ea ek moet stuur?",
    },
    "document_menu_1": {
        "en": "Latest invoice",
        "af": "Nuutste faktuur",
    },
    "document_menu_2": {
        "en": "Latest statement",
        "af": "Nuutste staat",
    },

    # --- Support sub-menu ---
    "support_menu_preamble": {
        "en": "What kind of issue are you experiencing?",
        "af": "Watter tipe probleem ondervind jy?",
    },
    "support_menu_1": {
        "en": "Connectivity / speed issue",
        "af": "Konneksie / spoed probleem",
    },
    "support_menu_2": {
        "en": "Billing or payment query",
        "af": "Rekening- of betalingsnavraag",
    },
    "support_menu_3": {
        "en": "General / other",
        "af": "Algemeen / ander",
    },

    # --- Invalid selection ---
    "invalid_selection": {
        "en": "I didn't recognise that option. Please reply with:",
        "af": "Ek het nie daardie opsie herken nie. Antwoord asseblief met:",
    },

    # --- Balance ---
    "balance_line": {
        "en": "{name} your account balance is *R{balance}*.",
        "af": "{name} jou rekeningbalans is *R{balance}*.",
    },
    "balance_name_prefix": {
        "en": "{first_name},",
        "af": "{first_name},",
    },
    "balance_anon_prefix": {
        "en": "Your",
        "af": "Jou",
    },
    "outstanding_line": {
        "en": "Outstanding invoices: *R{amount}* ({count} unpaid).",
        "af": "Uitstaande fakture: *R{amount}* ({count} onbetaal).",
    },
    "no_outstanding": {
        "en": "You have no outstanding invoices \u2705",
        "af": "Jy het geen uitstaande fakture nie \u2705",
    },

    # --- Invoices ---
    "no_unpaid_invoices": {
        "en": "Great news \u2014 you have no unpaid invoices! \u2705",
        "af": "Goeie nuus \u2014 jy het geen onbetaalde fakture nie! \u2705",
    },
    "unpaid_invoices_header": {
        "en": "You have *{count}* unpaid invoice{s} totalling *R{total}*:",
        "af": "Jy het *{count}* onbetaalde faktu{re} wat *R{total}* beloop:",
    },
    "invoices_more": {
        "en": "...and {n} more.",
        "af": "...en nog {n}.",
    },

    # --- Summary ---
    "summary_header": {
        "en": "*Account summary for {name}*",
        "af": "*Rekeningopsomming vir {name}*",
    },
    "summary_balance": {
        "en": "Balance: *R{balance}*",
        "af": "Balans: *R{balance}*",
    },
    "summary_outstanding": {
        "en": "Outstanding: *R{amount}*",
        "af": "Uitstaande: *R{amount}*",
    },

    # --- Invoice link ---
    "invoice_link_found": {
        "en": "Your latest unpaid invoice is *{num}* for *R{amount}*.\nI'll have our team send you the invoice link shortly.",
        "af": "Jou nuutste onbetaalde faktuur is *{num}* vir *R{amount}*.\nEk sal ons span vra om die faktuurskakel vir jou te stuur.",
    },
    "invoice_link_none": {
        "en": "I couldn't find any recent invoices on your account. Would you like me to check something else?",
        "af": "Ek kon geen onlangse fakture op jou rekening kry nie. Wil jy h\u00ea ek moet iets anders nagaan?",
    },

    # --- Statement link ---
    "statement_link": {
        "en": "Your current balance is *R{balance}*.\nI'll have our team send you a statement link shortly.",
        "af": "Jou huidige balans is *R{balance}*.\nEk sal ons span vra om 'n staatsskakel vir jou te stuur.",
    },

    # --- Latency ---
    "latency_ok_avg": {
        "en": "Ping to {host}: *{avg}ms* average. Connection looks OK from our side.",
        "af": "Ping na {host}: *{avg}ms* gemiddeld. Konneksie lyk reg van ons kant af.",
    },
    "latency_ok": {
        "en": "Ping to {host} was successful. Connection seems fine from our end.",
        "af": "Ping na {host} was suksesvol. Konneksie lyk reg van ons kant af.",
    },
    "latency_fail": {
        "en": "I couldn't reach {host} from our side either. This might indicate a wider issue. Our support team will investigate.",
        "af": "Ek kon nie {host} van ons kant af bereik nie. Dit kan 'n groter probleem aandui. Ons ondersteuningspan sal ondersoek.",
    },

    # --- Support ---
    "support_ack": {
        "en": "I'm sorry to hear you're having trouble. I've noted your message and our support team will follow up shortly. Is there anything else I can check for you, like your balance or invoices?",
        "af": "Ek is jammer om te hoor jy het probleme. Ek het jou boodskap genoteer en ons ondersteuningspan sal binnekort opvolg. Is daar iets anders wat ek vir jou kan nagaan, soos jou balans of fakture?",
    },
    "support_category_selected": {
        "en": "*{label}* \u2014 got it.\n\nPlease describe your issue briefly and I'll create a support ticket for you.",
        "af": "*{label}* \u2014 het dit.\n\nBeskryf asseblief jou probleem kortliks en ek sal 'n ondersteuningskaartjie vir jou skep.",
    },
    "support_ticket_created": {
        "en": "Your support ticket *#{ticket}* has been created.\nCategory: {category}\n\nOur team will follow up with you shortly. Is there anything else I can help with?",
        "af": "Jou ondersteuningskaartjie *#{ticket}* is geskep.\nKategorie: {category}\n\nOns span sal binnekort opvolg. Is daar iets anders waarmee ek kan help?",
    },
    "support_ticket_failed": {
        "en": "I'm sorry, I couldn't create the ticket right now. Please try again in a moment, or contact us directly for support.",
        "af": "Ek is jammer, ek kon nie nou die kaartjie skep nie. Probeer asseblief weer oor 'n oomblik, of kontak ons direk vir ondersteuning.",
    },
    "support_ticket_error": {
        "en": "I'm sorry, something went wrong creating your ticket. Please try again in a moment.",
        "af": "Ek is jammer, iets het fout gegaan met die skep van jou kaartjie. Probeer asseblief weer oor 'n oomblik.",
    },

    # --- Needs client ---
    "needs_client": {
        "en": "I couldn't match your number to an account on our system. Could you please share your account number or the name on your account?",
        "af": "Ek kon nie jou nommer aan 'n rekening op ons stelsel koppel nie. Kan jy asseblief jou rekeningnommer of die naam op jou rekening deel?",
    },

    # --- Error ---
    "generic_error": {
        "en": "Sorry, something went wrong on my side. Please try again in a moment.",
        "af": "Jammer, iets het by my fout gegaan. Probeer asseblief weer oor 'n oomblik.",
    },

    # --- Unknown ---
    "unknown_fallback": {
        "en": "I'm not sure I understand. Please reply with a number from the menu, or type something like *balance*, *invoices*, or *account summary*.",
        "af": "Ek is nie seker ek verstaan nie. Antwoord asseblief met 'n nommer van die kieslys, of tik iets soos *balans*, *fakture*, of *rekeningopsomming*.",
    },

    # --- Repeat ---
    "repeat_reply": {
        "en": "I just sent you that info. Is there anything else I can help with?",
        "af": "Ek het sopas daardie inligting gestuur. Is daar iets anders waarmee ek kan help?",
    },

    # --- Account lookup ---
    "lookup_error": {
        "en": "Sorry, I couldn't search for that right now. Please try again in a moment.",
        "af": "Jammer, ek kon nie daarvoor soek nie. Probeer asseblief weer oor 'n oomblik.",
    },
    "lookup_linked": {
        "en": "\u2705 Account *{number}* ({name}) linked.\n\n",
        "af": "\u2705 Rekening *{number}* ({name}) gekoppel.\n\n",
    },
    "lookup_verify_email": {
        "en": "I found account *{number}* ({name}).\n\nFor security, please confirm the email address on this account.\nHint: {masked}",
        "af": "Ek het rekening *{number}* ({name}) gevind.\n\nVir sekuriteit, bevestig asseblief die e-posadres op hierdie rekening.\nWenk: {masked}",
    },
    "lookup_multiple": {
        "en": "I found {count} accounts matching that. Could you share your account number? It usually starts with *DRA*.",
        "af": "Ek het {count} rekeninge gevind wat ooreenstem. Kan jy jou rekeningnommer deel? Dit begin gewoonlik met *DRA*.",
    },
    "lookup_none": {
        "en": "I couldn't find an account matching that. Please check and try again, or type *menu* to go back.",
        "af": "Ek kon nie 'n rekening kry wat ooreenstem nie. Kontroleer asseblief en probeer weer, of tik *menu* om terug te gaan.",
    },

    # --- Email verification ---
    "email_verified": {
        "en": "\u2705 Verified! Welcome, {name}.\n\n",
        "af": "\u2705 Geverifieer! Welkom, {name}.\n\n",
    },
    "email_failed": {
        "en": "That doesn't match the email on this account. For security, I can't provide account information.\n\nPlease send *Hi* to try again.",
        "af": "Dit stem nie ooreen met die e-pos op hierdie rekening nie. Vir sekuriteit kan ek nie rekeninginligting verskaf nie.\n\nStuur asseblief *Hi* om weer te probeer.",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """Look up a translated string.

    Falls back to English if key or language is missing.
    """
    entry = _STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en", key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text
