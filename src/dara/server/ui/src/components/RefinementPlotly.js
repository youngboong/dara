import createPlotlyComponent from 'react-plotly.js/factory';
import Plotly from 'plotly.js/lib/core';
import Scatter from 'plotly.js/lib/scatter';

// The refinement view only renders scatter traces. Importing react-plotly.js
// directly pulls every Plotly trace type (maps, 3-D, finance, etc.) into the
// lazy chunk and makes the first "Visualize result" click unnecessarily slow.
Plotly.register([Scatter]);

export default createPlotlyComponent(Plotly);
