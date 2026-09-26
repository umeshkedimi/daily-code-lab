"""Labeled retrieval questions over Python's language reference
(`pydoc_data.topics`, shipped with the interpreter).

Labels are EVIDENCE SPANS, not chunk ids: a retrieved chunk is relevant iff
it contains the span (whitespace-normalized). Chunk ids change whenever the
chunking strategy changes, and chunking is exactly what the experiments vary,
so id-based labels would have to be redone for every configuration.

Question types (`kind`):
  lexical      -- uses the passage's own distinctive vocabulary
  paraphrase   -- asks for the same fact in different words (vocabulary
                  mismatch: the hard case for keyword search)
  unanswerable -- the corpus does not contain the answer (tests abstention).
                  "far" = unrelated to Python; "near" = about Python, but a
                  library module that the language reference does not cover.

The questions were written after reading the passages, so they are biased
toward passages that exist; their `overlap` with the gold text is *measured*
in the demo rather than assumed (see notes.md).

LABEL AUDIT. A single gold span undercounts recall whenever the corpus states
the same fact in more than one place. The 13 questions that at least one
retriever missed at R@5 were audited by "pooling": the top-3 results of every
retriever were read and judged against the question, whichever system found
them. Three questions turned out to have a second, equally correct passage
(`alt_evidence`: P03, P04, P18). This is one annotator, who had seen the system
outputs; the demo therefore reports results under strict (original) labels
as well as audited ones.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Question:
    id: str
    kind: str  # lexical | paraphrase | unanswerable
    text: str
    topic: Optional[str]  # gold topic key in pydoc_data.topics (None if unanswerable)
    evidence: Optional[str]  # verbatim (whitespace-normalized) span inside that topic
    alt_evidence: Tuple[str, ...] = ()  # other passages that also correctly answer it (found by the label audit)


_L = "lexical"
_P = "paraphrase"
_U = "unanswerable"

QUESTIONS = [
    # --- lexical: shares the passage's own terms -------------------------------------
    Question("L01", _L, "What happens to the saved exception when a finally clause executes a return statement?", "try", "the saved exception is discarded"),
    Question("L02", _L, "Are exceptions raised in the else clause of a try statement handled by the preceding except clauses?", "try", 'are not handled by the preceding "except" clauses'),
    Question("L03", _L, "Which attribute stores the cause of an exception raised with the from clause?", "raise", 'attached to the raised exception as the "__cause__" attribute'),
    Question("L04", _L, "What arguments does __exit__ receive when the suite is exited because of an exception?", "with", 'its type, value, and traceback are passed as arguments to "__exit__()"'),
    Question("L05", _L, "In what order are stacked decorators applied?", "function", "Multiple decorators are applied in nested fashion"),
    Question("L06", _L, "What happens to the execution frame of a class body once it finishes?", "class", "its execution frame is discarded but its local namespace is saved"),
    Question("L07", _L, "Can yield from be used inside the body of a coroutine function?", "async", 'to use a "yield from" expression inside the body of a coroutine function'),
    Question("L08", _L, "Must names listed in a nonlocal statement already have bindings in an enclosing scope?", "nonlocal", "must refer to pre-existing bindings in an enclosing scope"),
    Question("L09", _L, "What does the code generator emit for an assert statement when optimization is requested?", "assert", "emits no code for an assert statement when optimization is requested"),
    Question("L10", _L, "Is the target of an augmented assignment evaluated once or twice?", "augassign", "The target is only evaluated once"),
    Question("L11", _L, "Which method is the reflection of __lt__?", "customization", '"__lt__()" and "__gt__()" are each other'),
    Question("L12", _L, "Under what condition is __getattr__ called?", "attribute-access", 'Called when the default attribute access fails with an "AttributeError"'),
    Question("L13", _L, "What does dict.setdefault return when the key is missing?", "typesmapping", "insert *key* with a value of *default* and return *default*"),
    Question("L14", _L, "What does str.partition return if the separator is not found?", "string-methods", "return a 3-tuple containing the string itself, followed by two empty strings"),
    Question("L15", _L, "What is the difference between step and next in the debugger?", "debugger", "executes called functions at (nearly) full speed"),
    Question("L16", _L, "Which constructs count as code blocks in a Python program?", "execmodel", "The following are blocks: a module, a function body, and a class definition"),
    Question("L17", _L, "Is y evaluated more than once in a chained comparison like x < y <= z?", "comparisons", 'except that "y" is evaluated only once'),
    Question("L18", _L, "What does a lambda expression yield?", "lambda", "yields a function object"),
    # --- paraphrase: same facts, different words --------------------------------------
    Question("P01", _P, "If I bind a caught error to a name, why can't I use that name after the handler block ends?", "try", "it is cleared at the end of the except clause"),
    Question("P02", _P, "How can I stop Python from showing the earlier failure when I raise a replacement error inside a handler?", "raise", 'explicitly suppressed by specifying "None"'),
    Question("P03", _P, "How can a context manager swallow an error that happened inside the block?", "with", "the exception is suppressed",
             ("the method wishes to suppress the exception (i.e., prevent it from being propagated), it should return a true value",)),
    Question("P04", _P, "Why does a list used as a default argument keep its contents between calls?", "function", "the expression is evaluated once, when the function is defined",
             ("a list or dictionary used as default value will be shared by all calls",)),
    Question("P05", _P, "If a class and one of its instances both define the same attribute name, which one wins when I look it up through self?", "class", "an instance attribute hides a class attribute with the same name"),
    Question("P06", _P, "Does a function declared with async def still produce a coroutine when its body never suspends?", "async", "always coroutine functions, even if they do not contain"),
    Question("P07", _P, "Can I assign to __debug__ to switch assertions off while the program runs?", "assert", 'Assignments to "__debug__" are illegal'),
    Question("P08", _P, "When I write x[k] += g(y), does Python fetch the existing item before or after calling g?", "augassign", 'first looks-up "a[i]", then it evaluates "f(x)"'),
    Question("P09", _P, "When comparing objects of different types, whose method is tried first if the right-hand class derives from the left-hand one?", "customization", "the reflected method of the right operand has priority"),
    Question("P10", _P, "If the interpreter finds an attribute through normal lookup, does the fallback hook still run?", "attribute-access", "the attribute is found through the normal mechanism"),
    Question("P11", _P, "How do I merge another mapping's entries into a dictionary, replacing values for keys that already exist?", "typesmapping", "overwriting existing keys"),
    Question("P12", _P, "How do I strip a leading piece off a string only when the string actually begins with it?", "string-methods", "If the string starts with the *prefix* string"),
    Question("P13", _P, "While stepping through code, how can I run forward until a chosen line number is reached?", "debugger", "continue execution until a line with a number greater or equal to that is reached"),
    Question("P14", _P, "Is each line I type at the interactive prompt treated as its own unit of execution?", "execmodel", "Each command typed interactively is a block"),
    Question("P15", _P, "What error do I get when I remove a variable name that was never assigned?", "del", 'If the name is unbound, a "NameError" exception will be raised'),
    Question("P16", _P, "Do I need a declaration to merely read a module-level variable inside a function, or only to assign to it?", "global", "free variables may refer to globals without being declared global"),
    Question("P17", _P, "What is the way to duplicate a list without duplicating the objects inside it, other than slicing?", "typesseq", "creates a shallow copy of *s*"),
    Question("P18", _P, "Does having yield anywhere inside a def change what calling that function returns?", "yield", "is sufficient to cause that definition to create a generator function",
             ('which uses the "yield" statement (see section The yield statement) is called a *generator function*',)),
    # --- unanswerable: far (unrelated) and near (Python, but not in the language reference)
    Question("U01", _U, "How do I configure the number of gunicorn worker processes?", None, None),
    Question("U02", _U, "What is the default port used by PostgreSQL?", None, None),
    Question("U03", _U, "Explain the syntax of lifetime annotations in Rust.", None, None),
    Question("U04", _U, "How do I pretty-print JSON with indentation using the json module?", None, None),
    Question("U05", _U, "How does asyncio.gather handle cancellation of pending tasks?", None, None),
    Question("U06", _U, "What does the timeout parameter of threading.Lock.acquire do?", None, None),
    Question("U07", _U, "How do I read rows as dictionaries with the csv module's DictReader?", None, None),
    Question("U08", _U, "How do I define subcommands with argparse subparsers?", None, None),
    Question("U09", _U, "How do I use field(default_factory=list) in a dataclass?", None, None),
    Question("U10", _U, "What is the difference between os.path.join and joining paths with pathlib?", None, None),
]

ANSWERABLE = [q for q in QUESTIONS if q.kind != _U]
UNANSWERABLE = [q for q in QUESTIONS if q.kind == _U]
