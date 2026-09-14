# Synthetic paper: a two-level avoided crossing

This is an original software test fixture, not a published research result.
It is distributed with the repository under the MIT license.

## 1. Question and model

Consider the real symmetric two-level Hamiltonian

$$H(\lambda)=\begin{pmatrix}\lambda & 1\\1 & -\lambda\end{pmatrix}.$$

Energies are measured in units of the off-diagonal coupling, which is fixed to 1.
The basis is ordered as the first and second matrix rows. There is no spatial
lattice or boundary-condition approximation in this finite-dimensional example.

## 2. Method

The characteristic polynomial is $E^2-(1+\lambda^2)$, so the ground-state energy
is $E_0=-\sqrt{1+\lambda^2}$ and the excited-state energy has the opposite sign.
This is an exact statement about the specified matrix, not an asymptotic fit.

## 3. Figure 1 and reference values

Figure 1 is the ground-state energy as a function of $\lambda$ on $[0,1]$.
For the test target, compare exactly these three reference points:

| lambda | ground-state energy |
|---|---|
| 0 | -1.0 |
| 0.5 | -1.118033988749895 |
| 1 | -1.4142135623730951 |

The reproduction must emit machine-readable values and a labelled curve. The
numeric acceptance criterion is maximum absolute error at these points at most
$10^{-12}$. There is no fitted rescaling, offset or interpolation in the test.
The plot must label both axes and identify the ground-state branch.

## 4. Interpretation and limits

The ground-state energy decreases on the nonnegative parameter interval. The
two levels do not cross because their separation is at least 2. A correct result
validates the synthetic reproduction workflow only; it establishes no claim about
a many-body system or any real paper. All parameter values and output requirements
are supplied here, and no network, external data or additional packages are needed.
