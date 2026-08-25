#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <new>
#include <string>
#include <vector>

#ifdef _PyCFunction_CAST
#define GAZETTEER_PY_CFUNCTION_CAST(function) _PyCFunction_CAST(function)
#else
#define GAZETTEER_PY_CFUNCTION_CAST(function)                               \
    reinterpret_cast<PyCFunction>(function)
#endif

namespace {

constexpr const char *CAPSULE_NAME = "gazetteer_matcher._fuzzy_native.ChoiceBatch";

struct Choice {
    std::u32string scoring;
    std::u32string original;
    int token_length;
};

struct ChoiceBatch {
    std::vector<Choice> choices;
};

struct Match {
    Py_ssize_t index;
    double score;
};

bool unicode_to_u32(PyObject *value, std::u32string &result) {
    if (!PyUnicode_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "choices must contain only strings");
        return false;
    }

    const Py_ssize_t length = PyUnicode_GET_LENGTH(value);
    const int kind = PyUnicode_KIND(value);
    const void *data = PyUnicode_DATA(value);
    try {
        result.resize(static_cast<std::size_t>(length));
    } catch (const std::bad_alloc &) {
        PyErr_NoMemory();
        return false;
    }
    for (Py_ssize_t index = 0; index < length; ++index) {
        result[static_cast<std::size_t>(index)] =
            static_cast<char32_t>(PyUnicode_READ(kind, data, index));
    }
    return true;
}

class DistanceWorkspace {
  public:
    std::size_t levenshtein(const std::u32string &left,
                            const std::u32string &right) {
        const std::u32string *rows = &left;
        const std::u32string *columns = &right;
        if (columns->size() > rows->size()) {
            std::swap(rows, columns);
        }

        previous_.resize(columns->size() + 1);
        current_.resize(columns->size() + 1);
        for (std::size_t column = 0; column <= columns->size(); ++column) {
            previous_[column] = column;
        }

        for (std::size_t row = 1; row <= rows->size(); ++row) {
            current_[0] = row;
            for (std::size_t column = 1; column <= columns->size(); ++column) {
                const std::size_t substitution =
                    previous_[column - 1] +
                    ((*rows)[row - 1] == (*columns)[column - 1] ? 0 : 1);
                current_[column] = std::min(
                    {current_[column - 1] + 1, previous_[column] + 1,
                     substitution});
            }
            previous_.swap(current_);
        }
        return previous_[columns->size()];
    }

    std::size_t damerau(const std::u32string &left,
                        const std::u32string &right) {
        const std::u32string *rows = &left;
        const std::u32string *columns = &right;
        if (columns->size() > rows->size()) {
            std::swap(rows, columns);
        }

        previous_previous_.resize(columns->size() + 1);
        previous_.resize(columns->size() + 1);
        current_.resize(columns->size() + 1);
        for (std::size_t column = 0; column <= columns->size(); ++column) {
            previous_[column] = column;
            previous_previous_[column] = column;
        }

        for (std::size_t row = 1; row <= rows->size(); ++row) {
            current_[0] = row;
            for (std::size_t column = 1; column <= columns->size(); ++column) {
                const std::size_t substitution =
                    previous_[column - 1] +
                    ((*rows)[row - 1] == (*columns)[column - 1] ? 0 : 1);
                current_[column] = std::min(
                    {current_[column - 1] + 1, previous_[column] + 1,
                     substitution});
                if (row > 1 && column > 1 &&
                    (*rows)[row - 1] == (*columns)[column - 2] &&
                    (*rows)[row - 2] == (*columns)[column - 1]) {
                    current_[column] =
                        std::min(current_[column],
                                 previous_previous_[column - 2] + 1);
                }
            }
            previous_previous_.swap(previous_);
            previous_.swap(current_);
        }
        return previous_[columns->size()];
    }

  private:
    std::vector<std::size_t> previous_previous_;
    std::vector<std::size_t> previous_;
    std::vector<std::size_t> current_;
};

void choice_batch_destructor(PyObject *capsule) {
    auto *batch = static_cast<ChoiceBatch *>(
        PyCapsule_GetPointer(capsule, CAPSULE_NAME));
    if (batch == nullptr) {
        PyErr_Clear();
        return;
    }
    delete batch;
}

PyObject *compile_choices(PyObject *, PyObject *args) {
    PyObject *scoring_object = nullptr;
    PyObject *original_object = nullptr;
    PyObject *lengths_object = nullptr;
    if (!PyArg_ParseTuple(args, "OOO", &scoring_object, &original_object,
                          &lengths_object)) {
        return nullptr;
    }

    PyObject *scoring =
        PySequence_Fast(scoring_object, "scoring choices must be a sequence");
    if (scoring == nullptr) {
        return nullptr;
    }
    PyObject *original =
        PySequence_Fast(original_object, "original choices must be a sequence");
    if (original == nullptr) {
        Py_DECREF(scoring);
        return nullptr;
    }
    PyObject *lengths =
        PySequence_Fast(lengths_object, "token lengths must be a sequence");
    if (lengths == nullptr) {
        Py_DECREF(scoring);
        Py_DECREF(original);
        return nullptr;
    }

    const Py_ssize_t length = PySequence_Fast_GET_SIZE(scoring);
    if (PySequence_Fast_GET_SIZE(original) != length ||
        PySequence_Fast_GET_SIZE(lengths) != length) {
        Py_DECREF(scoring);
        Py_DECREF(original);
        Py_DECREF(lengths);
        PyErr_SetString(PyExc_ValueError, "choice sequences must have equal lengths");
        return nullptr;
    }

    auto *batch = new (std::nothrow) ChoiceBatch();
    if (batch == nullptr) {
        Py_DECREF(scoring);
        Py_DECREF(original);
        Py_DECREF(lengths);
        return PyErr_NoMemory();
    }

    try {
        batch->choices.resize(static_cast<std::size_t>(length));
    } catch (const std::bad_alloc &) {
        delete batch;
        Py_DECREF(scoring);
        Py_DECREF(original);
        Py_DECREF(lengths);
        return PyErr_NoMemory();
    }

    for (Py_ssize_t index = 0; index < length; ++index) {
        Choice &choice = batch->choices[static_cast<std::size_t>(index)];
        const long token_length =
            PyLong_AsLong(PySequence_Fast_GET_ITEM(lengths, index));
        if (token_length == -1 && PyErr_Occurred()) {
            delete batch;
            Py_DECREF(scoring);
            Py_DECREF(original);
            Py_DECREF(lengths);
            return nullptr;
        }
        choice.token_length = static_cast<int>(token_length);
        if (!unicode_to_u32(PySequence_Fast_GET_ITEM(scoring, index),
                            choice.scoring) ||
            !unicode_to_u32(PySequence_Fast_GET_ITEM(original, index),
                            choice.original)) {
            delete batch;
            Py_DECREF(scoring);
            Py_DECREF(original);
            Py_DECREF(lengths);
            return nullptr;
        }
    }

    Py_DECREF(scoring);
    Py_DECREF(original);
    Py_DECREF(lengths);
    PyObject *capsule =
        PyCapsule_New(batch, CAPSULE_NAME, choice_batch_destructor);
    if (capsule == nullptr) {
        delete batch;
    }
    return capsule;
}

PyObject *extract_matches(PyObject *, PyObject *args, PyObject *kwargs) {
    PyObject *query_object = nullptr;
    PyObject *capsule = nullptr;
    double cutoff = 0.0;
    int limit = 3;
    const char *algorithm = "damerau";
    int query_tokens = -1;
    int extra_tokens = 0;
    static const char *keywords[] = {"query", "batch", "cutoff", "limit",
                                     "algorithm", "query_tokens",
                                     "extra_tokens", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "OO|disii",
                                     const_cast<char **>(keywords), &query_object,
                                     &capsule, &cutoff, &limit, &algorithm,
                                     &query_tokens, &extra_tokens)) {
        return nullptr;
    }
    if (!PyUnicode_Check(query_object)) {
        PyErr_SetString(PyExc_TypeError, "query must be a string");
        return nullptr;
    }
    if (limit <= 0) {
        return PyList_New(0);
    }

    const bool use_damerau = std::string(algorithm) == "damerau";
    if (!use_damerau && std::string(algorithm) != "levenshtein") {
        PyErr_Format(PyExc_ValueError, "Unknown fuzzy algorithm: %s", algorithm);
        return nullptr;
    }

    auto *batch = static_cast<ChoiceBatch *>(
        PyCapsule_GetPointer(capsule, CAPSULE_NAME));
    if (batch == nullptr) {
        return nullptr;
    }

    std::u32string query;
    if (!unicode_to_u32(query_object, query)) {
        return nullptr;
    }

    std::vector<Match> matches;
    std::exception_ptr native_error;
    Py_BEGIN_ALLOW_THREADS
    try {
        DistanceWorkspace workspace;
        matches.reserve(batch->choices.size());
        for (std::size_t index = 0; index < batch->choices.size(); ++index) {
            const Choice &choice = batch->choices[index];
            if (query_tokens >= 0 &&
                (choice.token_length < query_tokens - extra_tokens ||
                 choice.token_length > query_tokens + extra_tokens)) {
                continue;
            }
            const std::size_t denominator =
                std::max<std::size_t>({query.size(), choice.scoring.size(), 1});
            const std::size_t distance =
                use_damerau ? workspace.damerau(query, choice.scoring)
                             : workspace.levenshtein(query, choice.scoring);
            const double score =
                std::max(0.0, 1.0 - static_cast<double>(distance) /
                                           static_cast<double>(denominator));
            if (score >= cutoff) {
                matches.push_back(
                    {static_cast<Py_ssize_t>(index), score});
            }
        }
        std::stable_sort(matches.begin(), matches.end(),
                         [batch](const Match &left, const Match &right) {
                             if (left.score != right.score) {
                                 return left.score > right.score;
                             }
                             const Choice &left_choice =
                                 batch->choices[static_cast<std::size_t>(left.index)];
                             const Choice &right_choice = batch->choices[
                                 static_cast<std::size_t>(right.index)];
                             if (left_choice.original.size() !=
                                 right_choice.original.size()) {
                                 return left_choice.original.size() <
                                        right_choice.original.size();
                             }
                             return left_choice.original < right_choice.original;
                         });
    } catch (...) {
        native_error = std::current_exception();
    }
    Py_END_ALLOW_THREADS

    if (native_error != nullptr) {
        return PyErr_NoMemory();
    }

    const Py_ssize_t result_length = std::min<Py_ssize_t>(
        static_cast<Py_ssize_t>(matches.size()), limit);
    PyObject *result = PyList_New(result_length);
    if (result == nullptr) {
        return nullptr;
    }
    for (Py_ssize_t index = 0; index < result_length; ++index) {
        const Match &match = matches[static_cast<std::size_t>(index)];
        PyObject *item = Py_BuildValue("(nd)", match.index, match.score);
        if (item == nullptr) {
            Py_DECREF(result);
            return nullptr;
        }
        PyList_SET_ITEM(result, index, item);
    }
    return result;
}

PyMethodDef module_methods[] = {
    {"compile_choices", compile_choices, METH_VARARGS,
     "Compile normalized and original choices into a reusable native batch."},
    {"extract", GAZETTEER_PY_CFUNCTION_CAST(extract_matches),
     METH_VARARGS | METH_KEYWORDS,
     "Return the best matching choice indexes and scores."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "_fuzzy_native",
    "Native batch fuzzy matching for gazetteer-matcher.",
    -1,
    module_methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

} // namespace

PyMODINIT_FUNC PyInit__fuzzy_native() {
    return PyModule_Create(&module_definition);
}
