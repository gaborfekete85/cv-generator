<table class="header-table" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr>
    <td class="header-side header-qr" width="22%" valign="middle" align="left">
      {% if qr_data_uri %}
      <!-- Nested table so the xhtml2pdf fallback can stack the label
           neatly BELOW the QR. WeasyPrint uses the absolutely-positioned
           overlay (thought-bubble trail) and hides the fallback row. -->
      <table cellpadding="0" cellspacing="0" border="0">
        <tr><td align="center">
          <div class="qr-wrap">
            <img src="{{ qr_data_uri }}" alt="QR" width="110" height="110" />
            {% if qr_label %}
            <!-- Thought-bubble trail — rendered only by WeasyPrint, which
                 positions the overlay pill at the tip of the trail. -->
            <span class="qr-bubble qr-bubble-sm"></span>
            <span class="qr-bubble qr-bubble-md"></span>
            <span class="qr-badge-overlay">{{ qr_label }}</span>
            {% endif %}
          </div>
        </td></tr>
        {% if qr_label %}
        <tr><td align="center">
          <!-- xhtml2pdf-only fallback pill (hidden in WeasyPrint). -->
          <span class="qr-badge-below">{{ qr_label }}</span>
        </td></tr>
        {% endif %}
      </table>
      {% endif %}
    </td>
    <td class="header-main" width="56%" valign="middle" align="center">
      <div class="cv-name">{{ profile.get('name', '') }}</div>
      {% if profile.get('title') %}<div class="cv-subtitle">{{ profile.title }}</div>{% endif %}
      <div class="cv-contact">
        {% if profile.get('email') %}<a href="mailto:{{ profile.email }}">{{ profile.email }}</a>{% endif %}
        {% if profile.get('website') %} | <a href="{{ profile.website }}">{{ profile.website }}</a>{% endif %}
      </div>
      {% if profile.get('nationality') or profile.get('permit') or profile.get('birth_year') %}
      <div class="cv-contact">
        {%- set bits = [] -%}
        {%- if profile.get('nationality') %}{% set _ = bits.append(profile.nationality) %}{% endif -%}
        {%- if profile.get('permit') %}{% set _ = bits.append(profile.permit) %}{% endif -%}
        {%- if profile.get('birth_year') %}{% set _ = bits.append(profile.birth_year|string) %}{% endif -%}
        {{ bits | join(' | ') }}
      </div>
      {% endif %}
      <div class="cv-contact">
        {%- set loc_bits = [] -%}
        {%- if profile.get('location') %}{% set _ = loc_bits.append(profile.location) %}{% endif -%}
        {%- if profile.get('phone') %}{% set _ = loc_bits.append('Mobile ' + profile.phone) %}{% endif -%}
        {{ loc_bits | join(' | ') }}
      </div>
      {% if profile.get('linkedin') or profile.get('github') %}
      <div class="cv-contact">
        {% if profile.get('linkedin') %}<a href="{{ profile.linkedin }}">LinkedIn</a>{% endif %}
        {% if profile.get('linkedin') and profile.get('github') %} | {% endif %}
        {% if profile.get('github') %}<a href="{{ profile.github }}">GitHub</a>{% endif %}
      </div>
      {% endif %}
    </td>
    <td class="header-side header-photo" width="22%" valign="middle" align="right">
      <img src="{{ photo_data_uri }}" alt="Photo" width="110" height="110" />
    </td>
  </tr>
</table>

---

## Summary

{{ tailored_summary }}

{% if highlighted_skills %}
## Skills

{% for group, items in highlighted_skills.items() %}
**{{ group|replace('_',' ')|title }}:** {{ items|join(' · ') }}
{% endfor %}
{% endif %}

## Experience

{% for job in ordered_experience %}
### {{ job.get('role', '') }} — {{ job.get('company', '') }}
*{{ job.get('start', '') }} – {{ job.get('end', '') }}{% if job.get('location') %} &nbsp;·&nbsp; {{ job.location }}{% endif %}*

{% for h in (job.get('highlights') or []) %}
- {{ h }}
{% endfor %}

{% if job.get('keywords') %}*Stack:* {{ job.keywords|join(', ') }}{% endif %}

{% endfor %}

{% if profile.get('education') %}
## Education

{% for ed in profile.education %}
**{{ ed.get('degree', '') }}** — {{ ed.get('school', '') }}{% if ed.get('location') %}, {{ ed.location }}{% endif %}
*{{ ed.get('start', '') }} – {{ ed.get('end', '') }}*

{% endfor %}
{% endif %}

{% if profile.get('certifications') %}
## Certifications

{% for c in profile.certifications %}
- **{{ c.get('name', '') }}**{% if c.get('issuer') %} — {{ c.issuer }}{% endif %}{% if c.get('year') %} ({{ c.year }}){% endif %}
{% endfor %}
{% endif %}

{% if profile.get('projects') %}
## Projects

{% for p in profile.projects %}
{% if p is mapping %}
- **{{ p.get('name', '') }}**{% if p.get('description') %} — {{ p.description }}{% endif %}
{% else %}
- {{ p }}
{% endif %}
{% endfor %}
{% endif %}

{% if profile.get('languages') %}
## Languages

{% for lang in profile.languages %}
{% if lang is mapping %}
- **{{ lang.get('name', '') }}**{% if lang.get('level') %} — {{ lang.level }}{% endif %}
{% else %}
- {{ lang }}
{% endif %}
{% endfor %}
{% endif %}

{% if profile.get('hobbies') %}
## Hobbies

{% if profile.hobbies is iterable and profile.hobbies is not string %}
{{ profile.hobbies | join(' · ') }}
{% else %}
{{ profile.hobbies }}
{% endif %}
{% endif %}
