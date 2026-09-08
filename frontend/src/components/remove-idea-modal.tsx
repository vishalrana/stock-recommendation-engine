"use client";

import React, { useState } from 'react';
import { Trash2, X, Loader2, AlertCircle } from 'lucide-react';
import { Recommendation } from '../types/database';

interface RemoveIdeaModalProps {
  recommendation: Recommendation;
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (reason: string, note?: string) => Promise<{ success: boolean; error?: string }>;
}

const REASONS = [
  'Technical structure changed',
  'Thesis no longer valid',
  'Material negative development',
  'Other',
];

export default function RemoveIdeaModal({
  recommendation,
  isOpen,
  onClose,
  onConfirm,
}: RemoveIdeaModalProps) {
  const [reason, setReason] = useState<string>('Technical structure changed');
  const [note, setNote] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setIsSubmitting(true);
    try {
      const res = await onConfirm(reason, note.trim() || undefined);
      if (!res.success) {
        setErrorMessage(res.error || 'Failed to remove recommendation.');
      }
    } catch (err: any) {
      setErrorMessage(err.message || 'An unexpected error occurred.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/75 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4 backdrop-blur-xs transition-opacity duration-200"
      onClick={() => !isSubmitting && onClose()}
    >
      <div
        className="bg-[#121826] text-slate-100 rounded-t-3xl sm:rounded-2xl max-w-md w-full p-6 shadow-2xl border border-slate-800 relative animate-in fade-in slide-in-from-bottom-6 sm:zoom-in-95 duration-200 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between pb-4 border-b border-slate-800">
          <div>
            <h3 className="text-lg font-bold text-white flex items-center gap-2">
              <Trash2 className="w-5 h-5 text-rose-500" />
              Remove this stock idea?
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              This will move <span className="font-semibold text-slate-200">{recommendation.ticker}</span> to Closed Stock Ideas.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors disabled:opacity-50"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Error message if server action fails */}
        {errorMessage && (
          <div className="my-3 p-3 bg-rose-950/50 border border-rose-800 rounded-xl text-xs text-rose-300 flex items-start gap-2">
            <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0 mt-0.5" />
            <span>{errorMessage}</span>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          {/* Reason Selection */}
          <div>
            <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">
              Reason for Removal
            </label>
            <div className="space-y-2">
              {REASONS.map((r) => {
                const isSelected = reason === r;
                return (
                  <label
                    key={r}
                    className={`flex items-center gap-3 p-3 rounded-xl border text-xs font-medium cursor-pointer transition-all ${
                      isSelected
                        ? 'border-blue-500 bg-blue-950/40 text-blue-200'
                        : 'border-slate-800/80 bg-slate-900/60 hover:bg-slate-900 text-slate-300'
                    }`}
                  >
                    <input
                      type="radio"
                      name="removalReason"
                      value={r}
                      checked={isSelected}
                      onChange={(e) => setReason(e.target.value)}
                      className="text-blue-500 focus:ring-blue-500 bg-slate-800 border-slate-700"
                    />
                    <span>{r}</span>
                  </label>
                );
              })}
            </div>
          </div>

          {/* Optional Note */}
          <div>
            <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">
              Optional Note
            </label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Broke key support level on high volume..."
              rows={2}
              className="w-full text-xs p-3 bg-slate-900/80 border border-slate-800 rounded-xl outline-none focus:border-blue-500 text-slate-100 placeholder-slate-500 resize-none"
            />
          </div>

          {/* Non-blacklisting reminder */}
          <div className="p-3 bg-slate-900/40 border border-slate-800/60 rounded-xl text-[11px] text-slate-400 leading-relaxed">
            Removing this recommendation preserves historical records. This stock is <strong className="text-slate-300">not blacklisted</strong> and remains fully eligible for future scans.
          </div>

          {/* Action buttons */}
          <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-4 py-2.5 text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800 rounded-xl transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-5 py-2.5 text-xs font-bold text-white bg-rose-600 hover:bg-rose-700 rounded-xl shadow-md transition-colors flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Removing...</span>
                </>
              ) : (
                <span>Remove Idea</span>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
