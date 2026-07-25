/* Vector reductions.
 *
 * Each of these is a separate pass over the data, and variance calls mean,
 * which walks the span a second time. Nothing here is wrong; it is simply more
 * traversals than the arithmetic requires.
 */
#include "mstats.h"

double ms_vec_sum(const double *v, size_t n)
{
    double total = 0.0;
    for (size_t i = 0; i < n; i++) {
        total += v[i];
    }
    return total;
}

double ms_vec_mean(const double *v, size_t n)
{
    if (n == 0) {
        return 0.0;
    }
    /* A second traversal: ms_vec_sum has already walked this span. */
    return ms_vec_sum(v, n) / (double)n;
}

double ms_vec_variance(const double *v, size_t n)
{
    if (n == 0) {
        return 0.0;
    }
    const double mean = ms_vec_mean(v, n);
    double accumulated = 0.0;
    for (size_t i = 0; i < n; i++) {
        const double delta = v[i] - mean;
        accumulated += delta * delta;
    }
    return accumulated / (double)n;
}

double ms_vec_max(const double *v, size_t n)
{
    if (n == 0) {
        return 0.0;
    }
    double best = v[0];
    for (size_t i = 1; i < n; i++) {
        if (v[i] > best) {
            best = v[i];
        }
    }
    return best;
}
