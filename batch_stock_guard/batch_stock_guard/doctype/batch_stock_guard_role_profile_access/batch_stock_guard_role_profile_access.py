"""Controller retained so existing rows can be loaded during migration.

The DocType definition was intentionally removed. Keep this controller until all
sites have run the migration that converts role-profile access to role access.
"""

from frappe.model.document import Document


class BatchStockGuardRoleProfileAccess(Document):
	pass
