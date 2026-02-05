<script lang="ts">
  import { ContextMenu as ContextMenuPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = ContextMenuPrimitive.SeparatorProps & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<ContextMenuPrimitive.Separator
  class={cn('-mx-1 my-1 h-px bg-border', className)}
  {...restProps}
/>
