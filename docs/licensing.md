# Licensing

BudsLink Spatial Companion's original project code and documentation are licensed
under **GNU Affero General Public License version 3 only (`AGPL-3.0-only`)**.
Copyright © 2026 BudsLink Spatial Companion contributors.
The complete, unmodified [license text](../LICENSE) comes from
[GNU's official AGPLv3 text](https://www.gnu.org/licenses/agpl-3.0.txt).

## What that permits and requires

Commercial use, modification and sale are allowed. Redistribution carries the
license's notice and Corresponding Source obligations. If you modify the program
and your version supports remote interaction over a computer network, it must
prominently offer those users the Corresponding Source of that version without
charge. This is a sharing requirement, not a ban on businesses or paid services.
The [GNU license FAQ](https://www.gnu.org/licenses/gpl-faq.html) explains common
cases; the license text controls the terms.

This change applies to the current original-code release. Copies previously
released under MIT retain those permissions; the change does not revoke them.
The [legacy MIT notice](../LICENSES/MIT-legacy.txt) is retained for that history.

## Upstream work keeps its terms

| Component | License and retained notice |
|---|---|
| Derived Plasma Companion UI and patch | GPL-3.0-or-later, matching upstream; [GPL text](../LICENSES/GPL-3.0.txt), SPDX headers and [provenance](../packaging/companion-PROVENANCE.md) |
| SonyTrackerLinux helper and adapted protocol code | MIT; helper package includes upstream LICENSE, and the adapter keeps its copyright/permission notice |
| Adapted SlimeVR protocol code | MIT; notice remains in `spatial/trackers/slime.py` |
| Bundled AutoEQ profile | MIT; [AutoEQ notice](../spatial/data/AutoEq-LICENSE.txt) and [profile provenance](equalizer.md) |
| Omniphony, mpv, Harletty and other external dependencies | Their own licenses; see [dependency provenance](dependencies.md) and [package notes](../packaging/README.md) |

The AGPL notice does not replace upstream notices or claim ownership of third-party
code, music, trademarks or account services. Keep those notices when distributing
source or packages. The project is a downstream integration, not an official
BudsLink, Sony, TIDAL or Dolby product.
