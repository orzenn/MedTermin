from math import sqrt
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Method(BaseModel):
    title: str
    category: str
    description: str
    source: str
    units: dict[str, str]


class Calculation(Method):
    value: float | int | None
    label: str
    color: Literal["green", "yellow", "red", "neutral"]
    components: dict[str, str | float]
    note: str


class CalculatorMetadata(Method):
    name: str
    parameters: dict[str, dict]


class CalculatorCatalog(BaseModel):
    calculators: list[CalculatorMetadata]

Height = Annotated[float, Field(ge=100, le=250, allow_inf_nan=False, title="Рост (см)", description="Диапазон учебного калькулятора для взрослых.")]
Weight = Annotated[float, Field(ge=25, le=450, allow_inf_nan=False, title="Масса (кг)")]
Systolic = Annotated[float, Field(ge=40, le=300, allow_inf_nan=False, title="Систолическое АД (мм рт. ст.)")]
Diastolic = Annotated[float, Field(ge=10, le=200, allow_inf_nan=False, title="Диастолическое АД (мм рт. ст.)")]
HeartRate = Annotated[float, Field(ge=20, le=300, allow_inf_nan=False, title="ЧСС (уд/мин)")]
Eye = Annotated[Literal["4 — Спонтанно", "3 — На звук", "2 — На давление", "1 — Нет", "NT — Не оценивается"], Field(title="Открывание глаз (E)")]
Verbal = Annotated[Literal["5 — Ориентированная речь", "4 — Спутанная речь", "3 — Отдельные слова", "2 — Звуки", "1 — Нет", "NT — Не оценивается"], Field(title="Речевая реакция (V)")]
Motor = Annotated[Literal["6 — Выполняет команды", "5 — Локализует стимул", "4 — Нормальное сгибание", "3 — Аномальное сгибание", "2 — Разгибание", "1 — Нет", "NT — Не оценивается"], Field(title="Двигательная реакция (M)")]

CATALOG = {
    "bmi": {"title": "Индекс массы тела · BMI", "category": "Антропометрия", "description": "Отношение массы к квадрату роста. Категории CDC для взрослых от 20 лет.", "source": "https://www.cdc.gov/bmi/adult-calculator/bmi-categories.html", "units": {"height_cm": "см", "weight_kg": "кг", "result": "кг/м²"}},
    "body_surface_area": {"title": "Площадь поверхности тела · BSA", "category": "Антропометрия", "description": "Формула Мостеллера: квадратный корень из произведения роста и массы, делённого на 3600.", "source": "https://pubmed.ncbi.nlm.nih.gov/3657876/", "units": {"height_cm": "см", "weight_kg": "кг", "result": "м²"}},
    "glasgow_coma_scale": {"title": "Шкала комы Глазго · GCS", "category": "Неврология и реанимация", "description": "Сумма баллов за открывание глаз, речевую и двигательную реакцию, если оценены все три компонента.", "source": "https://pubmed.ncbi.nlm.nih.gov/4136544/", "units": {"eye": "баллы / NT", "verbal": "баллы / NT", "motor": "баллы / NT", "result": "баллы"}},
    "mean_arterial_pressure": {"title": "Среднее артериальное давление · MAP", "category": "Кардиология", "description": "Приближение: диастолическое АД плюс треть пульсового давления.", "source": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10200551/", "units": {"systolic": "мм рт. ст.", "diastolic": "мм рт. ст.", "result": "мм рт. ст."}},
    "shock_index": {"title": "Шоковый индекс · Shock Index", "category": "Неотложная медицина", "description": "Частота сердечных сокращений, делённая на систолическое АД.", "source": "https://pmc.ncbi.nlm.nih.gov/articles/PMC4193151/", "units": {"heart_rate": "уд/мин", "systolic": "мм рт. ст.", "result": "индекс"}},
}


def card(name: str, value: float | int | None, label: str, color: str, components: dict, note: str) -> Calculation:
    return Calculation(**CATALOG[name], value=value, label=label, color=color, components=components, note=note)


def bmi(height_cm: Height, weight_kg: Weight, adult_categories: Annotated[bool, Field(title="Показать категории ИМТ для взрослых от 20 лет")] = False) -> Calculation:
    """Индекс массы тела · BMI."""
    height_m = height_cm / 100
    value = weight_kg / height_m**2
    if not adult_categories:
        label, color = "ИМТ без возрастной категории", "neutral"
    elif value < 18.5:
        label, color = "Недостаточная масса", "yellow"
    elif value < 25:
        label, color = "Категория здоровой массы", "green"
    elif value < 30:
        label, color = "Избыточная масса", "yellow"
    else:
        label, color = "Категория ожирения", "red"
    return card("bmi", value, label, color, {"Масса, кг": weight_kg, "Рост, м": height_m, "Формула": "Масса / рост²"}, "Категории CDC относятся к взрослым от 20 лет. ИМТ — скрининговый показатель; интерпретация учитывает и другие данные.")


def body_surface_area(height_cm: Height, weight_kg: Weight) -> Calculation:
    """Площадь поверхности тела · BSA (Mosteller)."""
    value = sqrt(height_cm * weight_kg / 3600)
    return card("body_surface_area", value, "Расчёт по Мостеллеру", "neutral", {"Рост, см": height_cm, "Масса, кг": weight_kg, "Формула": "√(рост × масса / 3600)"}, "Расчёт даёт оценку площади поверхности тела. Не используйте эту учебную форму для расчёта доз.")


def glasgow_coma_scale(eye: Eye, verbal: Verbal, motor: Motor) -> Calculation:
    """Шкала комы Глазго · Glasgow Coma Scale (GCS)."""
    components = {"Глаза (E)": eye, "Речь (V)": verbal, "Движение (M)": motor}
    if any(value.startswith("NT") for value in components.values()):
        return card("glasgow_coma_scale", None, "Сумма не определяется", "yellow", components, "Сохраняйте оценки отдельных компонентов с пометкой NT. Не заменяйте NT единицей.")
    value = sum(int(item.split()[0]) for item in components.values())
    color = "green" if value == 15 else "yellow" if value > 8 else "red"
    label = "Максимальный балл" if value == 15 else "Сумма 9–14" if value > 8 else "Сумма 3–8"
    return card("glasgow_coma_scale", value, label, color, components, "Указывайте E, V и M отдельно. Цвет показывает диапазон баллов и не является диагнозом или показанием к вмешательству.")


def mean_arterial_pressure(systolic: Systolic, diastolic: Diastolic) -> Calculation:
    """Среднее артериальное давление · Mean Arterial Pressure (MAP)."""
    if systolic <= diastolic:
        raise ValueError("Систолическое АД должно быть выше диастолического. Проверьте значения и единицы.")
    pulse_pressure = systolic - diastolic
    return card("mean_arterial_pressure", diastolic + pulse_pressure / 3, "Приближённая оценка", "neutral", {"Систолическое АД": systolic, "Диастолическое АД": diastolic, "Пульсовое АД": pulse_pressure, "Формула": "ДАД + (САД − ДАД) / 3"}, "Формула приближённая. При изменённой частоте или ритме сердца оценка может отличаться от измеренного среднего АД.")


def shock_index(heart_rate: HeartRate, systolic: Systolic) -> Calculation:
    """Шоковый индекс · Shock Index (SI)."""
    return card("shock_index", heart_rate / systolic, "Отношение ЧСС к САД", "neutral", {"ЧСС, уд/мин": heart_rate, "Систолическое АД, мм рт. ст.": systolic, "Формула": "ЧСС / САД"}, "Учитывайте клинический контекст: единое пороговое значение не подходит для всех пациентов.")


STDIO_TOOLS = (bmi, body_surface_area, glasgow_coma_scale)
NETWORK_TOOLS = (*STDIO_TOOLS, mean_arterial_pressure, shock_index)
