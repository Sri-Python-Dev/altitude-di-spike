# Messages are fixed strings: exceptions must never carry document content.
class UnparseableDocument(Exception):
    reason = 'unparseable'
    def __str__(self):
        return 'Document cannot be parsed'

class PasswordProtectedDocument(UnparseableDocument):
    reason = 'password_protected'
    def __str__(self):
        return 'Document is password protected'

class CorruptDocument(UnparseableDocument):
    reason = 'corrupt'
    def __str__(self):
        return 'Document is corrupt'
