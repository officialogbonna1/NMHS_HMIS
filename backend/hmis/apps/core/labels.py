"""
Helpers for the strings a model renders *itself* as.

`__str__` is what Django labels every foreign-key dropdown, inline, breadcrumb
and admin log entry with — and it is called on unsaved instances too, because
a form's uniqueness error quotes the object it could not save. An
`auto_now_add` column is still None at that point, so formatting one straight
into an f-string turns a validation message into a `TypeError`. Everything
here is presentation only: nothing reads or writes a record.
"""

#: What an empty value reads as. An em dash, not a blank, so a label never
#: trails off into whitespace that looks like a truncation.
BLANK = "—"


def on(value, fmt="%d %b %Y"):
    """A date or time for a label — `BLANK` where there is not one yet."""
    return format(value, fmt) if value else BLANK


def money(value):
    """An amount for a label. No currency symbol: the hospital's is a setting
    (rule 31), and a label is not the place to load one."""
    return BLANK if value is None else f"{value:,.2f}"


def person(user):
    """A member of staff as a name — the same fallback the API's
    `*_by_name` fields use, so a label never shows a bare username where a
    screen would show "Jane Doe"."""
    if not user:
        return BLANK
    return user.get_full_name() or user.username
