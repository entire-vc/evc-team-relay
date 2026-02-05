<script lang="ts">
  import { Collapsible as CollapsiblePrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = CollapsiblePrimitive.ContentProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<CollapsiblePrimitive.Content
  class={cn('overflow-hidden data-[state=closed]:animate-collapsible-up data-[state=open]:animate-collapsible-down', className)}
  {...restProps}
>
  {@render children?.()}
</CollapsiblePrimitive.Content>
