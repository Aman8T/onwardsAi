/**
 * A work sample's material, as the template wrote it: code, a small table (tab-separated rows, the first row the head), or
 * plain lines. Always text nodes: the material is data, never markup.
 */
import type { WorkTask } from '../../../shared/interview';
import { FileIcon } from '../room/icons';

/** Rows of cells from tab-separated lines; blank lines are dropped. */
export function tableRows(material: string): string[][] {
  return material
    .split(/\r?\n/)
    .filter((line) => line.trim() !== '')
    .map((line) => line.split('\t').map((cell) => cell.trim()));
}

/** A cell that is a number (digits, commas, a decimal point), set in tabular figures and aligned right. */
const isNumeric = (cell: string) => /^[₹$€£]?\s*-?[\d,]+(\.\d+)?$/.test(cell);

export function Material({ task, caption }: { task: Pick<WorkTask, 'materialName' | 'material' | 'materialKind'>; caption?: string }) {
  const { materialName, material, materialKind } = task;
  return (
    <div className="rm-material">
      <div className="rm-material-name">
        <FileIcon className="rm-muted" />
        <span className="numeral rm-small">{materialName}</span>
      </div>
      {materialKind === 'code' && (
        <pre className="rm-code" role="region" tabIndex={0} aria-label={`${materialName}, the material`}>
          {material}
        </pre>
      )}
      {materialKind === 'table' && <MaterialTable name={materialName} material={material} />}
      {materialKind === 'text' && (
        <div className="rm-lines" role="region" tabIndex={0} aria-label={`${materialName}, the material`}>
          {material
            .split(/\r?\n/)
            .filter((line) => line.trim() !== '')
            .map((line, i) => (
              <p key={i}>{line}</p>
            ))}
        </div>
      )}
      {caption && <p className="rm-faint rm-small">{caption}</p>}
    </div>
  );
}

function MaterialTable({ name, material }: { name: string; material: string }) {
  const rows = tableRows(material);
  if (rows.length === 0) return null;
  const [head, ...body] = rows;
  return (
    <div className="rm-table-wrap" role="region" tabIndex={0} aria-label={`${name}, the material`}>
      <table className="rm-table">
        <thead>
          <tr>
            {head.map((cell, i) => (
              <th key={i} scope="col" className={body.every((r) => isNumeric(r[i] ?? '')) ? 'is-num' : undefined}>
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, r) => (
            <tr key={r}>
              {head.map((_, c) => (
                <td key={c} className={isNumeric(row[c] ?? '') ? 'is-num' : undefined}>
                  {row[c] ?? ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
