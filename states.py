from aiogram.fsm.state import State, StatesGroup


class SellerRegistration(StatesGroup):
    phone = State()


class AccountAdd(StatesGroup):
    login = State()
    password = State()
    price = State()


class SellerSearch(StatesGroup):
    code = State()


class TopUp(StatesGroup):
    amount = State()
    receipt = State()


class Withdraw(StatesGroup):
    amount = State()
    card = State()


class DisputeFlow(StatesGroup):
    reason = State()


class SellerAccount(StatesGroup):
    edit_price = State()


class AdminFlow(StatesGroup):
    user_query = State()
    broadcast_text = State()
    broadcast_confirm = State()
    balance_amount = State()
    balance_note = State()
