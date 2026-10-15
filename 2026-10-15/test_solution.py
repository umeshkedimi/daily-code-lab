import pytest

from solution import MyClassMethod, MyProperty, MyStaticMethod, Validated, one_of, positive


# --- MyProperty: basic behavior ----------------------------------------------------


def test_getter_returns_the_computed_value():
    class C:
        def _get_x(self):
            return 42

        x = MyProperty(_get_x)

    assert C().x == 42


def test_accessing_via_the_class_returns_the_descriptor_itself():
    class C:
        def _get_x(self):
            return 42

        x = MyProperty(_get_x)

    assert isinstance(C.x, MyProperty)


def test_no_setter_raises_attribute_error_with_the_exact_real_message():
    # Matches CPython 3.9's exact wording for a setter-less property.
    # (Later CPython versions reworded this to include the attribute and
    # class name -- this test pins the behavior of the Python actually
    # running this suite, not a version-independent guarantee.)
    class C:
        def _get_x(self):
            return 1

        x = MyProperty(_get_x)

    with pytest.raises(AttributeError, match="can't set attribute"):
        C().x = 5


def test_no_deleter_raises_attribute_error_with_the_exact_real_message():
    class C:
        def _get_x(self):
            return 1

        x = MyProperty(_get_x)

    with pytest.raises(AttributeError, match="can't delete attribute"):
        del C().x


def test_setter_decorator_chaining_works_like_the_real_property():
    class C:
        def __init__(self):
            self._x = 0

        @MyProperty
        def x(self):
            return self._x

        @x.setter
        def x(self, value):
            self._x = value

    c = C()
    c.x = 99
    assert c.x == 99


def test_deleter_decorator_chaining_works():
    class C:
        def __init__(self):
            self._x = "alive"

        @MyProperty
        def x(self):
            return self._x

        @x.deleter
        def x(self):
            self._x = "deleted"

    c = C()
    del c.x
    assert c._x == "deleted"


def test_doc_is_propagated_from_the_getter():
    class C:
        @MyProperty
        def x(self):
            """The x value."""
            return 1

    assert C.x.__doc__ == "The x value."


# --- the actual point: data descriptor vs. non-data descriptor precedence -------


def test_my_property_wins_over_an_instance_dict_entry_of_the_same_name():
    # A data descriptor (defines __set__) always wins over instance
    # __dict__, regardless of what's poked in there directly.
    class C:
        def _get_x(self):
            return "from the descriptor"

        x = MyProperty(_get_x)

    c = C()
    c.__dict__["x"] = "poked directly into instance __dict__"
    assert c.x == "from the descriptor"


def test_real_property_wins_over_an_instance_dict_entry_of_the_same_name():
    class C:
        @property
        def x(self):
            return "from the descriptor"

    c = C()
    c.__dict__["x"] = "poked directly into instance __dict__"
    assert c.x == "from the descriptor"


def test_my_classmethod_loses_to_an_instance_dict_entry_of_the_same_name():
    # A non-data descriptor (only __get__) loses to instance __dict__ --
    # the structural reason `self.method = something_else` can shadow a
    # plain method, but can never shadow a property the same way.
    class C:
        def _cm(cls):
            return "from the descriptor"

        cm = MyClassMethod(_cm)

    c = C()
    c.__dict__["cm"] = "poked directly into instance __dict__"
    assert c.cm == "poked directly into instance __dict__"


def test_real_classmethod_loses_to_an_instance_dict_entry_of_the_same_name():
    class C:
        @classmethod
        def cm(cls):
            return "from the descriptor"

    c = C()
    c.__dict__["cm"] = "poked directly into instance __dict__"
    assert c.cm == "poked directly into instance __dict__"


def test_my_staticmethod_loses_to_an_instance_dict_entry_of_the_same_name():
    class C:
        def _sm():
            return "from the descriptor"

        sm = MyStaticMethod(_sm)

    c = C()
    c.__dict__["sm"] = "poked directly into instance __dict__"
    assert c.sm == "poked directly into instance __dict__"


def test_a_plain_method_also_loses_to_an_instance_dict_entry():
    # Functions are themselves non-data descriptors (only __get__,
    # returning a bound method) -- this is why a plain method is
    # shadowable the same way, with no decorator involved at all.
    class C:
        def method(self):
            return "from the class"

    c = C()
    c.__dict__["method"] = "poked directly into instance __dict__"
    assert c.method == "poked directly into instance __dict__"


# --- MyClassMethod / MyStaticMethod parity with the real builtins --------------


@pytest.mark.parametrize("access", ["class", "instance"])
def test_classmethod_binds_to_the_class_both_ways(access):
    class Real:
        @classmethod
        def cm(cls):
            return cls.__name__

    class Mine:
        def _cm(cls):
            return cls.__name__

        cm = MyClassMethod(_cm)

    real_result = Real.cm() if access == "class" else Real().cm()
    mine_result = Mine.cm() if access == "class" else Mine().cm()
    assert real_result == "Real"
    assert mine_result == "Mine"


def test_classmethod_receives_the_subclass_when_called_on_a_subclass():
    class Base:
        def _cm(cls):
            return cls.__name__

        cm = MyClassMethod(_cm)

    class Derived(Base):
        pass

    assert Derived.cm() == "Derived"
    assert Base.cm() == "Base"


@pytest.mark.parametrize("access", ["class", "instance"])
def test_staticmethod_is_unbound_both_ways(access):
    class Real:
        @staticmethod
        def sm(x, y):
            return x + y

    class Mine:
        def _sm(x, y):
            return x + y

        sm = MyStaticMethod(_sm)

    real_result = Real.sm(2, 3) if access == "class" else Real().sm(2, 3)
    mine_result = Mine.sm(2, 3) if access == "class" else Mine().sm(2, 3)
    assert real_result == mine_result == 5


def test_classmethod_passes_through_extra_arguments():
    class C:
        def _cm(cls, a, b, *, c):
            return (cls.__name__, a, b, c)

        cm = MyClassMethod(_cm)

    assert C.cm(1, 2, c=3) == ("C", 1, 2, 3)


# --- Validated: a descriptor that solves a real problem property doesn't --------


def test_set_name_derives_the_private_storage_name():
    class Account:
        balance = Validated(positive, "must be positive")

    assert Account.__dict__["balance"].private_name == "_balance"


def test_valid_value_is_stored_and_retrievable():
    class Account:
        balance = Validated(positive, "must be positive")

        def __init__(self, balance):
            self.balance = balance

    assert Account(100).balance == 100


def test_invalid_value_raises_value_error_and_does_not_mutate_the_stored_value():
    class Account:
        balance = Validated(positive, "must be positive")

        def __init__(self, balance):
            self.balance = balance

    acct = Account(100)
    with pytest.raises(ValueError, match="must be positive"):
        acct.balance = -5
    assert acct.balance == 100  # the invalid assignment left the old value intact


def test_one_of_predicate_rejects_values_outside_the_allowed_set():
    class Account:
        status = Validated(one_of("active", "frozen", "closed"), "must be active/frozen/closed")

        def __init__(self, status):
            self.status = status

    acct = Account("active")
    with pytest.raises(ValueError):
        acct.status = "vip"
    assert acct.status == "active"


def test_two_validated_attributes_on_one_class_do_not_collide():
    class Account:
        balance = Validated(positive, "must be positive")
        status = Validated(one_of("active", "frozen"), "must be active/frozen")

        def __init__(self, balance, status):
            self.balance = balance
            self.status = status

    acct = Account(50, "frozen")
    assert (acct.balance, acct.status) == (50, "frozen")
    assert Account.__dict__["balance"].private_name == "_balance"
    assert Account.__dict__["status"].private_name == "_status"


def test_two_instances_of_the_same_class_have_independent_values():
    # The classic descriptor pitfall: storing state ON the descriptor
    # object itself (instead of in the owning instance's __dict__) would
    # make every instance of the class share one value. Verified this
    # does NOT happen here.
    class Account:
        balance = Validated(positive, "must be positive")

        def __init__(self, balance):
            self.balance = balance

    a = Account(100)
    b = Account(200)
    assert (a.balance, b.balance) == (100, 200)
    a.balance = 150
    assert (a.balance, b.balance) == (150, 200)  # changing a did not change b


def test_the_same_validated_instance_reused_across_two_classes_stays_independent():
    # __set_name__ is called once per class body it appears in, so even
    # sharing one Validated object across two unrelated classes is safe
    # -- each class's instances still store into their own __dict__.
    shared_validator = Validated(positive, "must be positive")

    class Wallet:
        amount = shared_validator

        def __init__(self, amount):
            self.amount = amount

    class Invoice:
        amount = shared_validator

        def __init__(self, amount):
            self.amount = amount

    w = Wallet(10)
    i = Invoice(20)
    assert (w.amount, i.amount) == (10, 20)
    w.amount = 999
    assert (w.amount, i.amount) == (999, 20)


def test_accessing_validated_via_the_class_returns_the_descriptor_itself():
    class Account:
        balance = Validated(positive, "must be positive")

    assert isinstance(Account.balance, Validated)
