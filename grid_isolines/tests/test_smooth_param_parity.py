"""4.0.1: параметры сглаживания FPDEMS одинаковы в 2.01 и 2.04.

Инструмент 2.01 (DemDownloadAlgorithm) и 2.04 (TopoFillDepressionsAlgorithm)
оба сглаживают рельеф методом FPDEMS. Раньше 2.01 звал smooth_fpdems на
дефолтах, не выводя окно/порог/проходы в интерфейс, - пользователь видел в
2.04 больше настроек при том же сглаживании. Тест стережёт паритет: если
в один инструмент параметр добавили, а в другой забыли, тест падает.

Разбор идёт по исходному тексту (AST), QGIS не требуется.
"""
import ast
import os
import unittest

SMOOTH_PARAMS = {"SMOOTH_FILTER", "SMOOTH_DIFF", "SMOOTH_ITER"}
SMOOTH_KW = {"filter_size", "norm_diff_deg", "elev_iters"}


def _class_node(name):
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "algorithms.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("класс не найден: " + name)


def _string_consts(node):
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _smooth_call_kwargs(node):
    """kwargs вызова smooth_fpdems внутри класса (пустое множество, если нет)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "smooth_fpdems":
                return {kw.arg for kw in n.keywords}
    return set()


class TestSmoothParamParity(unittest.TestCase):

    def test_both_declare_all_three_params(self):
        for cls in ("DemDownloadAlgorithm", "TopoFillDepressionsAlgorithm"):
            consts = _string_consts(_class_node(cls))
            missing = SMOOTH_PARAMS - consts
            self.assertFalse(
                missing, "%s не объявляет параметры сглаживания: %s"
                % (cls, sorted(missing)))

    def test_both_pass_all_three_to_smooth(self):
        for cls in ("DemDownloadAlgorithm", "TopoFillDepressionsAlgorithm"):
            kwargs = _smooth_call_kwargs(_class_node(cls))
            missing = SMOOTH_KW - kwargs
            self.assertFalse(
                missing, "%s не передаёт в smooth_fpdems: %s"
                % (cls, sorted(missing)))


if __name__ == "__main__":
    unittest.main()
