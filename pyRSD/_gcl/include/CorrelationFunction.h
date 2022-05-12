#ifndef CORRELATION_FUNCTION_H
#define CORRELATION_FUNCTION_H

#include "Common.h"
#include "parray.h"

// enum the integration method for Xi_lm
namespace IntegrationMethods { enum Type {FFTLOG=0, SIMPS, TRAPZ}; }

/* Functions to compute the quantity
      \xi_l^m(r) = \int_0^\infty \frac{dk}{2\pi^2} k^m j_l(kr) P(k) 
*/

// use FFTLog -- k, Pk will be interpolated onto a log-spaced grid
parray ComputeXiLM(int l, int m, const parray& k, const parray& pk, 
                      const parray& r, double smoothing, IntegrationMethods::Type method=IntegrationMethods::FFTLOG);
  
// use FFTlog, given log-spaced input k, Pk -- fills r, xi 
void ComputeXiLM_fftlog(int l, int m, const parray& k, const parray& pk, 
                          double r[], double xi[], double q=0, double smoothing=0.);

/*  Compute the correlation function xi(r) from a power spectrum P(k), this is 
    just Xi_0^2 in the notation above*/
parray pk_to_xi(int ell, const parray& k, const parray& pk, const parray& r, 
                  double smoothing, IntegrationMethods::Type method=IntegrationMethods::FFTLOG);

/* Compute the power spectrum P(k) from a correlation function xi(r), sampled
 * at logarithmically spaced points r[i]. */
parray xi_to_pk(int ell, const parray& r, const parray& xi, const parray& k, 
                  double smoothing, IntegrationMethods::Type method=IntegrationMethods::FFTLOG);


//-------------------------------------------------------------------------------------
class CorrelationFunction {
public:
    CorrelationFunction(const PowerSpectrum& P, double kmin = 1e-4, double kmax = 1e1);

    double Evaluate(double r) const;
    double operator()(double r) const { return Evaluate(r); }

    parray EvaluateMany(const parray& r) const;
    parray operator()(const parray& r) const { return EvaluateMany(r); }

    const PowerSpectrum& GetPowerSpectrum() const { return P; }
    double GetKmin() const { return kmin; }
    void SetKmin(double kmin_) { kmin=kmin_; }
    double GetKmax() const { return kmax; }
    void SetKmax(double kmax_) { kmax=kmax_; }

protected:
    const PowerSpectrum& P;
    double kmin, kmax;
};

#endif // CORRELATION_FUNCTION_H
